"""
batch_run.py - zip 안의 영상을 하나씩 임시로 풀어 후보 추출(run_pipeline)을 일괄 실행하고, 영상별 요약 CSV 와 조건별 집계표를 만든다

  python batch_run.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --out runs/batch1 --angles a --tmp E:\\tmp
  python batch_run.py ... --sites erip,ydsp --limit 5            # 일부만 먼저 시험
  python batch_run.py ... --set persist_sec=8 --preset both       # 설정 바꿔 다시(결과 폴더는 새로 지정)

- 도중에 멈춰도 다시 실행하면 끝난 영상은 건너뛴다(--force 로 다시 처리).
- 영상마다 out/<영상이름>/ 에 후보 폴더와 pipeline_summary.json, run.log 가 남는다. 영상 파일은 처리 직후 지워진다.
- 집계표의 '큰 후보 있음'은 정답과 비교한 정확도가 아니라 '면적이 일정 이상인 지속 후보가 하나라도 나온 영상의 비율'이라는 참고 지표다.
  정확도는 정답(사람이 확인한 봉투 위치)이 있어야 계산할 수 있다.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import list_videos, open_video, read_csv  # noqa: E402

HERE = Path(__file__).resolve().parent
COLS = ["file", "event_id", "site", "weather", "tod", "device", "angle", "ok", "candidates", "max_area", "first_sec",
        "length_sec", "elapsed_sec", "stabilize", "shake_p95_px"]


def stats(out_dir):
    c = []
    for p in sorted((out_dir / "candidates").glob("ID*/meta.json")):
        try:
            c.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    areas = [x.get("area_px", 0) for x in c]
    firsts = [x.get("detected_at_sec", 0) for x in c]
    return len(c), (max(areas) if areas else 0), (min(firsts) if firsts else "")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--preset", default="both", choices=["butt", "litter", "both"])
    p.add_argument("--angles", default="a", help="처리할 각도(쉼표 구분, 기본 a). 각도 3개를 모두 쓰려면 a,b,c")
    p.add_argument("--sites", default="", help="처리할 장소 코드(쉼표 구분, 기본 전체)")
    p.add_argument("--limit", type=int, default=0, help="처리할 영상 수 상한(시험용)")
    p.add_argument("--set", action="append", default=[], metavar="key=value", help="run_pipeline 의 --set 으로 전달")
    p.add_argument("--tmp", default=None)
    p.add_argument("--onboard-sec", type=float, default=0, help="N초 > 0 이면 각 영상에 자동 온보딩 적용(run_pipeline --onboard-sec)")
    p.add_argument("--verify-end", action="store_true", help="후보마다 영상 끝 장면 지지도(end_support)를 기록(run_pipeline --verify-end)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--large-area", type=int, default=1500, help="'큰 후보'로 볼 최소 면적(px²), 참고 지표용")
    a = p.parse_args()

    rows = list(read_csv(a.meta))
    angles = {x.strip() for x in a.angles.split(",") if x.strip()}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    rows = [r for r in rows if r["angle"] in angles and (not sites or r["site"] in sites)]
    if a.limit:
        rows = rows[:a.limit]
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "results.csv"
    done = {}
    if csv_path.exists() and not a.force:
        for r in read_csv(csv_path):
            done[r["file"]] = r
    results = dict(done)
    print(f"처리 대상 {len(rows)}개 (이미 끝난 {sum(1 for r in rows if r['file'] in done)}개는 건너뜀), 프리셋 {a.preset}, 각도 {sorted(angles)}")

    def flush():
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fp:
            wr = csv.DictWriter(fp, fieldnames=COLS)
            wr.writeheader()
            for r in results.values():
                wr.writerow({k: r.get(k, "") for k in COLS})

    t_all = time.time()
    for i, r in enumerate(rows, 1):
        if r["file"] in done:
            continue
        if r["file"] not in items:
            print(f"[{i}/{len(rows)}] zip 에서 찾을 수 없음: {r['file']}")
            continue
        key = Path(r["file"].split("::")[-1]).stem
        od = out / key
        t0 = time.time()
        with open_video(items[r["file"]], a.tmp) as vp:
            cmd = [sys.executable, str(HERE / "run_pipeline.py"), "--video", vp, "--out", str(od), "--preset", a.preset, "--no-analyze"]
            for kv in a.set:
                cmd += ["--set", kv]
            if a.verify_end:
                cmd += ["--verify-end"]
            if a.onboard_sec > 0:
                cmd += ["--onboard-sec", str(a.onboard_sec)]
            od.mkdir(parents=True, exist_ok=True)
            with open(od / "run.log", "w", encoding="utf-8") as lg:
                rc = subprocess.run(cmd, stdout=lg, stderr=subprocess.STDOUT).returncode
        summ = {}
        if (od / "pipeline_summary.json").exists():
            summ = json.loads((od / "pipeline_summary.json").read_text(encoding="utf-8"))
        n, amax, first = stats(od) if rc == 0 else (0, 0, "")
        shake = (summ.get("shake") or {}).get("p95_px", "")
        results[r["file"]] = {**{k: r[k] for k in ("file", "event_id", "site", "weather", "tod", "device", "angle")},
                              "ok": int(rc == 0), "candidates": n, "max_area": amax, "first_sec": first,
                              "length_sec": summ.get("length_sec", ""), "elapsed_sec": round(time.time() - t0, 1),
                              "stabilize": int(bool((summ.get("config") or {}).get("stabilize"))), "shake_p95_px": shake}
        flush()
        print(f"[{i}/{len(rows)}] {key}  {'성공' if rc == 0 else '실패(run.log 확인)'}  후보 {n}개, 최대 면적 {amax}, {time.time() - t0:.0f}초")
    flush()

    ok = [r for r in results.values() if str(r.get("ok")) == "1"]
    print(f"\n완료: 성공 {len(ok)}개 / 전체 {len(results)}개, 총 {(time.time() - t_all) / 60:.1f}분(이번 실행)")
    if not ok:
        return

    def agg(title, key):
        g = defaultdict(list)
        for r in ok:
            g[r[key]].append(r)
        print(f"\n[{title}별] 영상 수 / 평균 후보 수 / 큰 후보(≥{a.large_area}px²) 있는 영상 비율 / 평균 처리 시간(초)")
        for k in sorted(g, key=lambda x: -len(g[x])):
            v = g[k]
            big = sum(1 for r in v if float(r["max_area"] or 0) >= a.large_area) / len(v) * 100
            print(f"  {k:6} {len(v):4} / {sum(float(r['candidates']) for r in v) / len(v):5.1f} / {big:4.0f}% / {sum(float(r['elapsed_sec']) for r in v) / len(v):5.0f}")
    agg("장소", "site")
    agg("날씨(추정)", "weather")
    agg("시간대(추정)", "tod")
    print(f"\n영상별 결과: {csv_path}\n※ '큰 후보 있음'은 참고 지표이며 정확도가 아닙니다(정답 필요).")


if __name__ == "__main__":
    main()
