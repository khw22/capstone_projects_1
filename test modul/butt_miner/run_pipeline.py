"""
run_pipeline.py - 영상 하나를 [흔들림 측정 -> 후보 추출 -> 후보 요약 -> (서버 업로드)]까지 한 명령으로 실행

  python run_pipeline.py --video 영상.mp4 --out runs/결과 --preset litter
  python run_pipeline.py --video 영상.mp4 --out runs/결과 --preset butt --server http://localhost:8000 --device-id local-ai-server

  --preset  butt   꽁초용(작은 물체), litter  쓰레기용(큰 물체), both  둘 다(넓은 크기 범위). 모두 '시작점'이며 영상에 맞춰 조정해야 한다.
  --config  카메라별 설정 JSON(butt_miner mine --config 와 같은 형식). 프리셋 위에 덮어쓴다.
  --set     key=value 로 개별 값 덮어쓰기 (예: --set persist_sec=5 --set exclude_person=false)
  --ignore-region x,y,w,h   제외 영역 (여러 번 지정 가능)
  --stabilize auto|on|off   auto 는 흔들림을 재서 95% 이동량이 0.7px 이상이면 보정을 켠다.

사용한 설정은 out/pipeline_config.json, 요약은 out/pipeline_summary.json 에 저장된다(실험 기록용).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure_shake  # noqa: E402

PRESETS = {
    "butt": {"diff_thr": 20, "min_area": 6, "persist_sec": 10, "exclude_person": True},
    "litter": {"diff_thr": 20, "min_area": 300, "max_area": 40000, "neighbor_ratio": 0,
               "dedupe_px": 60, "persist_sec": 8, "exclude_person": True},
    # 꽁초와 쓰레기를 한 번에: 작은 물체(꽁초)와 큰 물체(봉투)가 모두 후보가 되는 넓은 크기 범위(시작점)
    "both": {"diff_thr": 20, "min_area": 40, "max_area": 40000, "neighbor_ratio": 0,
             "dedupe_px": 60, "persist_sec": 10, "exclude_person": True},
}


def parse_value(s):
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def run(cmd):
    print("\n>", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd]).returncode


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--preset", choices=list(PRESETS), default="butt")
    p.add_argument("--config", default=None)
    p.add_argument("--set", action="append", default=[], metavar="key=value")
    p.add_argument("--ignore-region", action="append", default=[], metavar="x,y,w,h")
    p.add_argument("--stabilize", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--debug-video", action="store_true", help="debug.mp4 저장(느려짐)")
    p.add_argument("--verify-end", action="store_true", help="후보마다 '영상 끝 장면에도 배경과 달라져 있는 정도(end_support)'를 계산해 meta.json 에 기록(이 데이터는 투기물이 끝까지 남음)")
    p.add_argument("--onboard-sec", type=float, default=0, help="N초 > 0 이면 자동 온보딩: 영상 앞 N초(쓰레기 없는 구간)에서 흔들리는 구역을 학습해 제외 영역을 자동 추가")
    p.add_argument("--no-analyze", action="store_true")
    p.add_argument("--server", default="")
    p.add_argument("--device-id", default="local-ai-server")
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1) 영상 정보
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"영상을 열 수 없습니다: {a.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    length = total / fps if fps else 0
    print(f"[1/4] 영상: {int(cap.get(3))}x{int(cap.get(4))}, {fps:.1f}fps, 약 {length:.0f}초")
    cap.release()

    # 2) 설정 합치기: 프리셋 < 설정 파일 < --set < --ignore-region
    cfg = dict(PRESETS[a.preset])
    if a.config:
        cfg.update(json.loads(Path(a.config).read_text(encoding="utf-8")))
    for kv in a.set:
        if "=" not in kv:
            raise SystemExit(f"--set 은 key=value 형식입니다: {kv}")
        k, v = kv.split("=", 1)
        cfg[k.strip().replace("-", "_")] = parse_value(v.strip())
    if a.ignore_region:
        cfg["ignore_region"] = list(cfg.get("ignore_region", [])) + a.ignore_region
    if 0 < length < 60 and "bg_interval" not in cfg:
        cfg.update({"bg_interval": 1, "bg_min": 8})
        print(f"      영상이 60초보다 짧아 배경 수집을 줄였습니다(bg_interval=1, bg_min=8). {'20초 미만이면 후보가 안 나올 수 있습니다.' if length < 20 else ''}")

    # 3) 흔들림 측정
    shake = None
    if a.stabilize == "on":
        cfg["stabilize"] = True
    elif a.stabilize == "auto" and "stabilize" not in cfg:
        m = measure_shake.measure(a.video, every=5, max_frames=600)
        if m["mags"]:
            p95 = float(np.percentile(m["mags"], 95))
            shake = {"mean_px": round(float(np.mean(m["mags"])), 2), "p95_px": round(p95, 2), "max_px": round(float(np.max(m["mags"])), 2)}
            cfg["stabilize"] = p95 >= 0.7
            print(f"[2/4] 흔들림: 평균 {shake['mean_px']}px, 95% {shake['p95_px']}px, 최대 {shake['max_px']}px -> 보정 {'켬' if cfg['stabilize'] else '끔'}")
    else:
        print(f"[2/4] 흔들림 보정: {'켬' if cfg.get('stabilize') else '끔'}")
    if not cfg.get("stabilize"):
        cfg.pop("stabilize", None)

    onb_info = None
    if a.onboard_sec > 0:
        import auto_onboard
        res = auto_onboard.learn(a.video, a.onboard_sec)
        if res is None:
            print("[3/4] 자동 온보딩: 앞부분 프레임이 부족해 건너뜀")
        else:
            cv2.imwrite(str(out / "onboarding.jpg"), auto_onboard.draw(res), [cv2.IMWRITE_JPEG_QUALITY, 90])
            cfg["ignore_region"] = list(cfg.get("ignore_region", [])) + res["ignore_region"]
            if res["ignore_region"]:
                cfg.setdefault("ignore_margin", 30)
            onb_info = {"sec": res["sec_used"], "zones": len(res["ignore_region"]), "ignored_fraction": res["ignored_fraction"],
                        "noise_floor": res["noise_floor"], "threshold": res["threshold"]}
            print(f"      자동 온보딩: 앞 {res['sec_used']}초로 제외 영역 {onb_info['zones']}개(화면의 {res['ignored_fraction'] * 100:.1f}%) 추가 -> {out / 'onboarding.jpg'}")
    cfg_path = out / "pipeline_config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      사용 설정 저장: {cfg_path}")

    # 4) 후보 추출
    print("[3/4] 후보 추출 중...")
    cmd = [sys.executable, HERE / "butt_miner.py", "mine", "--video", a.video, "--out", out, "--config", cfg_path]
    if a.debug_video:
        cmd += ["--debug-video", out / "debug.mp4"]
    t0 = time.time()
    if run(cmd) != 0:
        raise SystemExit("후보 추출에 실패했습니다. 위 오류 메시지를 확인하세요.")
    elapsed = time.time() - t0
    n = len(list((out / "candidates").glob("ID*")))
    speed = f"영상 {length:.0f}초를 {elapsed:.0f}초에 처리 (실시간의 {length / elapsed:.1f}배)" if elapsed > 0 and length else f"{elapsed:.0f}초"
    print(f"\n      후보 {n}개, {speed}")

    verify_info = None
    if a.verify_end and n:
        import gt_tools
        em = gt_tools.end_mask(a.video)
        if em is None:
            print("      끝 장면 확인: 프레임이 부족해 건너뜀")
        else:
            mk = em["mask"]
            sups = []
            for mp in sorted((out / "candidates").glob("ID*/meta.json")):
                try:
                    mj = json.loads(mp.read_text(encoding="utf-8"))
                    x, y, w_, h_ = mj["bbox_xywh"]
                    x, y = max(0, x), max(0, y)
                    reg = mk[y:y + h_, x:x + w_]
                    mj["end_support"] = round(float((reg > 0).mean()) if reg.size else 0.0, 3)
                    mp.write_text(json.dumps(mj, ensure_ascii=False, indent=2), encoding="utf-8")
                    sups.append(mj["end_support"])
                except Exception:
                    pass
            if sups:
                verify_info = {"n": len(sups), "ge_0.3": sum(1 for v in sups if v >= 0.3)}
                print(f"      끝 장면 확인: 후보 {len(sups)}개 중 end_support 0.3 이상 {verify_info['ge_0.3']}개")
    # 5) 요약과 업로드
    if n and not a.no_analyze:
        run([sys.executable, HERE / "analyze_candidates.py", "--candidates", out / "candidates", "--top-large", 5])
    uploaded = None
    if n and a.server:
        print("[4/4] 서버 업로드")
        uploaded = run([sys.executable, HERE / "upload_candidates.py", "--candidates", out / "candidates", "--server", a.server,
                        "--device-id", a.device_id, "--video-name", Path(a.video).name]) == 0

    summary = {"video": a.video, "length_sec": round(length, 1), "preset": a.preset, "config": cfg, "shake": shake,
               "onboarding": onb_info, "verify_end": verify_info, "candidates": n, "elapsed_sec": round(elapsed, 1),
               "realtime_factor": round(length / elapsed, 2) if elapsed > 0 and length else None, "uploaded": uploaded}
    (out / "pipeline_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n요약 저장: {out / 'pipeline_summary.json'}")
    if n:
        print("다음: 대시보드에서 후보를 분류한 뒤 pull_reviews.py -> butt_miner.py export 로 학습셋을 만드세요.")
    else:
        print("후보가 0개입니다. 영상 길이, --max-area/--min-area, 제외 영역, 흔들림 보정을 확인하세요.")


if __name__ == "__main__":
    main()
