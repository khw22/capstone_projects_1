"""
prepare_upload.py - GitHub 에 올릴 파일만 골라서(허용 목록) 클론한 저장소로 복사

  1) 팀 저장소를 새로 클론:  git clone <주소> cctv-work   (ZIP 으로 받은 폴더에는 .git 이 없어 올릴 수 없다)
  2) 미리보기(복사 안 함):   python prepare_upload.py --src C:\\Users\\LG\\Desktop\\cctv-main --dst C:\\Users\\LG\\Desktop\\cctv-work
  3) 실제 복사:             python prepare_upload.py --src ... --dst ... --apply --patch-dashboard

- 허용 목록에 있는 파일만 복사한다(영상, runs/, 모델 가중치, DB 같은 큰·민감한 파일은 구조적으로 제외).
- 서버 파일(models/storage/main)은 팀 저장소의 현재 파일과 비교해 바뀐 줄 수를 보여 준다. 팀원이 그 사이 고친 내용이 있으면
  복사 후 git diff 로 반드시 확인한다.
- index.html, script.js 는 복사하지 않고 --patch-dashboard 로 클론한 저장소의 '최신' 파일에 패치를 적용한다(팀원의 수정을 덮어쓰지 않기 위해).
"""
import argparse
import difflib
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_DIR = "test modul/butt_miner"
TOOL_FILES = ["butt_miner.py", "run_pipeline.py", "make_synthetic.py", "make_gt.py", "size_sweep.py", "analyze_candidates.py",
              "measure_shake.py", "upload_candidates.py", "pull_reviews.py", "detect_litter.py", "apply_dashboard_patch.py",
              "send_test_event.py", "cleanup_db.py", "check_setup.py", "prepare_upload.py", "README.md", "requirements.txt", ".gitignore"]
SERVER_FILES = ["server/api/models.py", "server/api/storage.py", "server/api/main.py"]
DASH_FILES = ["server/static_dashboard/butt_candidates.js"]


def read(p):
    return p.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True, help="지금 작업한 폴더(cctv-main)")
    p.add_argument("--dst", required=True, help="새로 클론한 저장소 폴더")
    p.add_argument("--apply", action="store_true", help="실제로 복사(없으면 미리보기)")
    p.add_argument("--patch-dashboard", action="store_true", help="클론한 저장소의 index.html/script.js 에 패치 적용")
    a = p.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    if not src.is_dir() or not dst.is_dir():
        sys.exit("--src, --dst 폴더가 있어야 합니다.")
    if not (dst / ".git").exists():
        print("[경고] dst 에 .git 이 없습니다. git clone 한 폴더가 맞는지 확인하세요(ZIP 으로 받은 폴더는 올릴 수 없음).")

    plan = []
    for f in TOOL_FILES:
        s = src / TOOL_DIR / f
        if not s.exists() and f == ".gitignore" and (src / TOOL_DIR / "gitignore").exists():
            s = src / TOOL_DIR / "gitignore"          # 다운로드 때 점이 빠진 이름
        plan.append((s, dst / TOOL_DIR / f, "도구"))
    for f in SERVER_FILES:
        plan.append((src / f, dst / f, "서버"))
    for f in DASH_FILES:
        plan.append((src / f, dst / f, "대시보드"))

    total = 0
    print(f"{'구분':6} {'상태':8} {'크기':>8}  파일")
    missing, to_copy = [], []
    for s, d, kind in plan:
        rel = d.relative_to(dst).as_posix()
        if not s.exists():
            missing.append(rel)
            print(f"{kind:6} {'원본없음':8} {'-':>8}  {rel}")
            continue
        size = s.stat().st_size
        total += size
        if not d.exists():
            status = "새 파일"
        elif read(s) == read(d):
            status = "동일"
        else:
            a_, b_ = read(d).splitlines(), read(s).splitlines()
            diff = list(difflib.unified_diff(a_, b_, lineterm="", n=0))
            plus = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
            minus = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
            status = f"+{plus}/-{minus}"
        flag = "  <-- 2MB 초과, 확인" if size > 2e6 else ""
        print(f"{kind:6} {status:8} {size / 1024:7.1f}K  {rel}{flag}")
        if status != "동일":
            to_copy.append((s, d))
    print(f"\n복사 대상 {len(to_copy)}개, 전체 {total / 1024:.0f}KB")
    if missing:
        print("[주의] 원본이 없는 파일:", missing)

    gi = dst / ".gitignore"
    if gi.exists() and "data/" not in read(gi) and "data" not in read(gi):
        print("[경고] 저장소 루트 .gitignore 에 data/ 가 없습니다. DB(cctv.db, 백업)가 올라가지 않는지 git status 로 확인하세요.")
    sj = dst / "server/static_dashboard/script.js"
    if sj.exists():
        import re
        if re.search(r"[0-9a-f]{32}", read(sj)):
            print("[주의] script.js 에 32자리 키(날씨 API 키로 보임)가 이미 저장소에 있습니다. 팀원에게 알려 폐기·재발급하세요.")

    if not a.apply:
        print("\n미리보기입니다. 복사하려면 --apply 를 붙이세요(서버 파일의 +/- 줄 수가 예상보다 크면 팀원이 고친 내용이 덮어써질 수 있으니 먼저 확인).")
        return
    for s, d in to_copy:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    print(f"복사 완료: {len(to_copy)}개")
    if a.patch_dashboard:
        r = subprocess.run([sys.executable, str(dst / TOOL_DIR / "apply_dashboard_patch.py"), "--dir",
                            str(dst / "server/static_dashboard"), "--no-backup"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
    print("\n다음: cd 저장소 && git status && git diff --stat  으로 올라갈 내용을 확인한 뒤 커밋하세요.")


if __name__ == "__main__":
    main()
