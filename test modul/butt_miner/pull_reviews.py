"""
pull_reviews.py - 대시보드에서 검수한 결과를 받아 reviewed/ 폴더로 정리 (butt_miner.py export 의 입력)

  python pull_reviews.py --server http://localhost:8000 --device-id local-ai-server \
      --candidates runs/결과/candidates --reviewed reviewed

  대시보드 라벨        -> 폴더
  꽁초 (cigarette_butt) -> reviewed/good/ID****
  쓰레기 아님 (not_litter) -> reviewed/noise/ID****      (Hard Negative)
  기타 쓰레기 (other_litter) -> reviewed/other_litter/ID**** (export 대상 아님)
  불확실 (uncertain)     -> reviewed/uncertain/ID****     (export 대상 아님)

  후보 폴더는 복사만 하고 원본은 지우지 않는다. 이미 reviewed 에 있으면 건너뛴다.
  upload_candidates.py 가 남긴 candidates/uploaded.json 으로 후보 폴더와 서버의 후보 ID 를 연결한다.
"""
import argparse
import json
import shutil
from pathlib import Path

import requests

FOLDER = {"cigarette_butt": "good", "not_litter": "noise", "other_litter": "other_litter", "uncertain": "uncertain"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--device-id", default="local-ai-server")
    p.add_argument("--candidates", required=True)
    p.add_argument("--reviewed", default="reviewed")
    p.add_argument("--timeout", type=float, default=30)
    a = p.parse_args()

    root = Path(a.candidates)
    log = root / "uploaded.json"
    if not log.exists():
        raise SystemExit(f"uploaded.json 이 없습니다. upload_candidates.py 로 먼저 올린 폴더인지 확인하세요: {root.resolve()}")
    uploaded = json.loads(log.read_text(encoding="utf-8"))

    r = requests.get(f"{a.server.rstrip('/')}/api/devices/{a.device_id}/reviews", timeout=a.timeout)
    if not r.ok:
        raise SystemExit(f"검수 결과를 가져오지 못했습니다 (HTTP {r.status_code}). 서버에 검수 기능 패치가 적용됐는지 확인하세요.")
    labels = {x["candidate_id"]: x["label"] for x in r.json().get("reviews", [])}

    counts, skipped, pending = {}, 0, 0
    for name, info in sorted(uploaded.items()):
        label = labels.get(info.get("frame_id"))
        if not label:
            pending += 1
            continue
        src = root / name
        dst = Path(a.reviewed) / FOLDER[label] / name
        if not src.is_dir():
            print(f"[건너뜀] 원본 폴더 없음: {src}")
            continue
        if dst.exists():
            skipped += 1
            continue
        shutil.copytree(src, dst)
        counts[FOLDER[label]] = counts.get(FOLDER[label], 0) + 1

    print("복사:", counts or "없음", f"| 이미 있음 {skipped} | 미검수 {pending}")
    print(f"다음: python butt_miner.py export --reviewed {a.reviewed} --dataset datasets/butt --prefix 영상이름")


if __name__ == "__main__":
    main()
