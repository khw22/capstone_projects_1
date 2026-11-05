"""
condition_sheet.py - 파일 이름에서 '추정'한 조건(날씨·시간대·기기)이 실제 영상과 맞는지, 조건별로 영상 몇 개를 뽑아 눈으로 확인

  python condition_sheet.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --out runs/cond --per 3

조건 값(예: 날씨 su/cl/ra)마다 서로 다른 사건에서 각도 a 영상을 최대 N개 골라 가운데 장면을 한 장으로 모은다.
조건별 평균 밝기, 해상도, fps 도 표로 보여 준다(zip 영상은 하나씩 임시로 풀었다 지움).
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import list_videos, open_video, read_csv  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--meta", required=True, help="aihub_meta.py 가 만든 meta.csv")
    p.add_argument("--out", default="runs/cond")
    p.add_argument("--per", type=int, default=3, help="조건 값마다 뽑을 영상 수")
    p.add_argument("--tmp", default=None)
    p.add_argument("--keys", default="weather,tod,device", help="확인할 조건(쉼표 구분)")
    a = p.parse_args()
    rows = list(read_csv(a.meta))
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    for key in [k.strip() for k in a.keys.split(",") if k.strip()]:
        groups = defaultdict(list)
        seen = set()
        for r in sorted(rows, key=lambda r: (r[key], r["angle"] != "a", r["event_id"])):
            if r["file"] in items and (r[key], r["event_id"]) not in seen:
                seen.add((r[key], r["event_id"]))
                groups[r[key]].append(r)
        print(f"\n[{key}] 값: 뽑은 영상 수 / 평균 밝기(0~255) / 해상도 / fps")
        sheet_rows = []
        for val in sorted(groups):
            cells, bright, res, fps_set = [], [], set(), set()
            for r in groups[val][:a.per]:
                with open_video(items[r["file"]], a.tmp) as vp:
                    cap = cv2.VideoCapture(vp)
                    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n // 2))
                    ok, f = cap.read()
                    res.add(f"{int(cap.get(3))}x{int(cap.get(4))}")
                    fps_set.add(round(cap.get(cv2.CAP_PROP_FPS), 1))
                    cap.release()
                if not ok:
                    continue
                bright.append(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()))
                c = cv2.resize(f, (320, 180))
                cv2.putText(c, f"{key}={val} {r['site']} {r['angle']}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
                cells.append(c)
            if not cells:
                continue
            print(f"  {val:6} {len(cells)}개 / {np.mean(bright):6.1f} / {sorted(res)} / {sorted(fps_set)}")
            while len(cells) < a.per:
                cells.append(np.zeros((180, 320, 3), np.uint8))
            sheet_rows.append(np.hstack(cells))
        if sheet_rows:
            cv2.imwrite(str(out / f"sheet_{key}.jpg"), np.vstack(sheet_rows))
            print(f"  저장: {out / f'sheet_{key}.jpg'}")
    print("\n사진의 각 줄이 조건 값 하나입니다. 비(ra) 줄에 실제로 비가 오는지, 야간(nig)이 어두운지, 기기(DF1/DF4)의 화질·해상도가 다른지 확인하세요.")


if __name__ == "__main__":
    main()
