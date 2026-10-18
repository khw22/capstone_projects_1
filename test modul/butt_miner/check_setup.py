"""
check_setup.py - 발표·시연 전에 파일과 환경이 맞는지 한 번에 점검

  python check_setup.py --repo C:\\Users\\LG\\Desktop\\cctv-main
  python check_setup.py --repo C:\\Users\\LG\\Desktop\\cctv-main --server http://localhost:8000 --device-id local-ai-server --model best.pt

[OK] 정상, [주의] 동작은 하지만 확인 필요, [실패] 고쳐야 함. 파일을 바꾸지 않고 읽기만 한다.
"""
import argparse
import importlib
import re
import subprocess
import sys
from pathlib import Path

res = {"OK": 0, "주의": 0, "실패": 0}


def say(kind, msg):
    res[kind] += 1
    print(f"[{kind}] {msg}")


def find_repo(arg):
    if arg:
        return Path(arg)
    p = Path.cwd().resolve()
    for q in [p] + list(p.parents)[:4]:
        if (q / "server" / "api" / "main.py").exists():
            return q
    return None


def help_text(script, *args):
    try:
        r = subprocess.run([sys.executable, str(script), *args, "--help"], capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr
    except Exception as e:
        return f"ERROR {e}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=None, help="저장소 루트(server 폴더가 있는 곳)")
    p.add_argument("--tools", default=None, help="도구 폴더(기본: 이 스크립트가 있는 폴더)")
    p.add_argument("--server", default="")
    p.add_argument("--device-id", default="local-ai-server")
    p.add_argument("--model", default="")
    a = p.parse_args()
    tools = Path(a.tools) if a.tools else Path(__file__).resolve().parent

    print("== 1. 파이썬 패키지 ==")
    for mod, need in (("cv2", True), ("numpy", True), ("requests", True), ("ultralytics", False)):
        try:
            importlib.import_module(mod)
            say("OK", f"{mod} 설치됨")
        except Exception:
            say("실패" if need else "주의", f"{mod} 없음 -> pip install {'opencv-python' if mod == 'cv2' else mod}"
                + ("" if need else " (사람 제외, 자동 감지에 필요)"))

    print("\n== 2. 도구 파일 ==")
    need = ["butt_miner.py", "run_pipeline.py", "make_synthetic.py", "make_gt.py", "size_sweep.py", "analyze_candidates.py",
            "measure_shake.py", "upload_candidates.py", "pull_reviews.py", "detect_litter.py", "apply_dashboard_patch.py",
            "send_test_event.py", "cleanup_db.py"]
    for f in need:
        say("OK" if (tools / f).exists() else "실패", f"{f}" + ("" if (tools / f).exists() else " 없음"))

    print("\n== 3. 최신 버전인지(옵션 존재 확인) ==")
    checks = [("butt_miner.py", ("mine",), ["--stabilize", "--dedupe-px", "--config", "--ignore-region"]),
              ("butt_miner.py", ("export",), ["--litter-class", "--yaml-path", "--bbox-scale"]),
              ("detect_litter.py", (), ["--gt", "--tile", "--event-conf"]),
              ("run_pipeline.py", (), ["--preset", "--stabilize"])]
    for script, sub, flags in checks:
        if not (tools / script).exists():
            continue
        txt = help_text(tools / script, *sub)
        miss = [f for f in flags if f not in txt]
        label = f"{script} {' '.join(sub)}".strip()
        say("OK" if not miss else "실패", f"{label}: " + ("필요한 옵션 모두 있음" if not miss else f"옵션 없음 {miss} -> 새 파일로 덮어쓰세요"))

    repo = find_repo(a.repo)
    print("\n== 4. 서버·대시보드 파일 ==")
    if not repo:
        say("주의", "저장소 루트를 찾지 못했습니다 (--repo 로 지정)")
    else:
        def has(path, text):
            f = repo / path
            return f.exists() and text in f.read_text(encoding="utf-8", errors="ignore")
        for path, text, what in [("server/api/models.py", "ReviewIn", "검수 모델(ReviewIn)"),
                                 ("server/api/storage.py", "set_review", "검수 저장(set_review)"),
                                 ("server/api/main.py", "/reviews", "검수 엔드포인트(/reviews)"),
                                 ("server/static_dashboard/butt_candidates.js", "butt-tools", "대시보드 요약 카드·필터"),
                                 ("server/static_dashboard/butt_candidates.js", "__buttRefresh", "대시보드 새로고침 버튼(최신 버전)"),
                                 ("server/static_dashboard/butt_candidates.js", "POLLUTION", "대시보드 오염 감지 지수(최신 버전)"),
                                 ("server/static_dashboard/index.html", "butt_candidates.js", "index.html 에 butt_candidates.js 연결"),
                                 ("server/static_dashboard/index.html", 'id="butt-table"', "index.html 후보 표 섹션"),
                                 ("server/static_dashboard/script.js", "renderButtCandidates", "script.js 에서 후보 표시 호출")]:
            say("OK" if has(path, text) else "실패", what + ("" if has(path, text) else f" 없음 ({path})"))
        sj = repo / "server/static_dashboard/script.js"
        if sj.exists() and re.search(r"[0-9a-f]{32}", sj.read_text(encoding="utf-8", errors="ignore")):
            say("주의", "script.js 에 32자리 키(날씨 API 키로 보임)가 있습니다. GitHub 에 올리기 전에 폐기·재발급하고 코드에서 빼세요")
        baks = list((repo / "server/static_dashboard").glob("*.bak"))
        if baks:
            say("주의", f".bak 백업 파일이 있습니다({', '.join(b.name for b in baks)}). GitHub 에 올리지 말고 동작 확인 후 지우세요")

    if a.model:
        print("\n== 5. 모델 ==")
        m = Path(a.model)
        if not m.exists():
            m = tools / a.model
        say("OK" if m.exists() else "실패", f"{a.model}: " + (f"{m.stat().st_size / 1e6:.1f}MB" if m.exists() else "파일 없음"))

    if a.server:
        print("\n== 6. 서버 ==")
        try:
            import requests
            r = requests.get(a.server.rstrip("/") + "/api/devices", timeout=5)
            ids = [d["device_id"] for d in r.json().get("devices", [])]
            say("OK", f"서버 응답, 등록된 장치 {ids}")
            say("OK" if a.device_id in ids else "실패", f"장치 ID '{a.device_id}' " + ("등록됨" if a.device_id in ids else "미등록 -> --device-id 를 위 목록 중 하나로"))
            r2 = requests.get(f"{a.server.rstrip('/')}/api/devices/{a.device_id}/reviews", timeout=5)
            say("OK" if r2.ok else "실패", f"검수 API(/reviews) 응답 {r2.status_code}" + ("" if r2.ok else " -> 서버 패치 적용 여부 확인, 서버 재시작"))
            # 서버가 실제로 주는 대시보드 스크립트가 저장소의 파일과 같은지(다른 폴더의 서버·캐시 문제 진단)
            rj = requests.get(f"{a.server.rstrip('/')}/butt_candidates.js", timeout=5)
            if not rj.ok:
                say("주의", f"서버에서 butt_candidates.js 를 받지 못했습니다({rj.status_code}). 서버가 다른 폴더의 대시보드를 쓰고 있을 수 있습니다")
            else:
                served = rj.content.decode("utf-8", errors="ignore")
                feats = [f for f in ("POLLUTION", "__buttRefresh") if f not in served]
                local = (repo / "server/static_dashboard/butt_candidates.js") if repo else None
                same = bool(local and local.exists() and local.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n") == served.replace("\r\n", "\n"))
                if feats:
                    say("실패", f"서버가 주는 butt_candidates.js 가 구버전입니다(없는 기능 {feats}). 서버를 켠 폴더가 --repo 와 같은지, "
                                "다른 서버가 8000번 포트를 쓰고 있지 않은지 확인하세요")
                elif local and not same:
                    say("주의", "서버가 주는 butt_candidates.js 가 저장소의 파일과 다릅니다. 서버를 다시 켜고 브라우저는 Ctrl+F5 를 누르세요")
                else:
                    say("OK", "서버가 주는 butt_candidates.js 가 최신입니다(브라우저에서 안 바뀌면 Ctrl+F5)")
        except Exception as e:
            say("실패", f"서버에 연결할 수 없습니다({a.server}): {e}")

    print(f"\n결과: OK {res['OK']}, 주의 {res['주의']}, 실패 {res['실패']}")
    sys.exit(1 if res["실패"] else 0)


if __name__ == "__main__":
    main()
