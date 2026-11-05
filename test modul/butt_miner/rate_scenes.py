"""
rate_scenes.py - 영상 끝 장면을 한 장씩 보고 '쓰레기가 얼마나 있는지' 1~4 등급을 키보드로 매긴다 (C2 오염 지수를 검증할 독립적인 정답)

  python rate_scenes.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --out runs/ratings --rater me

  화면에서 키:  1 = 거의 없음   2 = 소량   3 = 보통   4 = 많음      b = 이전으로   s = 건너뜀   q 또는 ESC = 저장하고 종료
- 순서는 무작위이고, 후보 상자·시나리오 이름·점수는 보여 주지 않는다(평가가 편향되지 않게). 영상당 약 5초.
- 결과는 runs/ratings/ratings_<평가자>.csv (key, rating). 다시 실행하면 이미 매긴 영상은 건너뛴다.
- 가능하면 두 사람이 따로(--rater a, --rater b) 매기면 평가자 간 일치도도 볼 수 있다.
- 매긴 뒤: python pollution_index.py --batch runs/batch_c1 --meta runs/meta/meta.csv --min-support 0.3 --ratings "runs/ratings/ratings*.csv"
"""
import argparse
import csv
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import list_videos, open_video, read_csv  # noqa: E402


def last_frame(vp):
    cap = cv2.VideoCapture(vp)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 3))
    ok, f = cap.read()
    cap.release()
    return f if ok else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--out", default="runs/ratings")
    p.add_argument("--rater", default="me")
    p.add_argument("--angles", default="a")
    p.add_argument("--sites", default="")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--tmp", default=None)
    p.add_argument("--keys", default="", help=argparse.SUPPRESS)          # 화면 없이 시험하기 위한 키 입력(쉼표 구분)
    a = p.parse_args()
    rows = read_csv(a.meta)
    angles = {x.strip() for x in a.angles.split(",") if x.strip()}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    rows = [r for r in rows if r["angle"] in angles and (not sites or r["site"] in sites)]
    random.Random(a.seed).shuffle(rows)                                       # 순서를 섞어 시나리오·장소 순서에 휘둘리지 않게 함
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"ratings_{a.rater}.csv"
    ratings = {r["key"]: r["rating"] for r in read_csv(path)} if path.exists() else {}
    key_of = lambda r: Path(r["file"].split("::")[-1]).stem
    todo = [r for r in rows if key_of(r) not in ratings and r["file"] in items]
    if a.limit:
        todo = todo[:a.limit]
    print(f"등급을 매길 영상 {len(todo)}개(이미 매긴 {len(ratings)}개). 키: 1 거의 없음 / 2 소량 / 3 보통 / 4 많음 / b 이전 / s 건너뜀 / q 종료")
    scripted = [k.strip() for k in a.keys.split(",") if k.strip()]

    def save():
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(["key", "rating"])
            w.writerows(sorted(ratings.items()))

    def getkey():
        if scripted:
            return ord(scripted.pop(0)[0])
        return cv2.waitKey(0) & 0xFF

    if not scripted:
        cv2.namedWindow("rate", cv2.WINDOW_NORMAL)
    i, history = 0, []
    while i < len(todo):
        r = todo[i]
        with open_video(items[r["file"]], a.tmp) as vp:
            f = last_frame(vp)
        if f is None:
            i += 1
            continue
        s = 1100 / f.shape[1]
        img = cv2.resize(f, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        bar = np.zeros((46, img.shape[1], 3), np.uint8)
        cv2.putText(bar, f"[{i + 1}/{len(todo)}] 1:none/very little  2:little  3:some  4:a lot   b:back  s:skip  q:quit", (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
        if not scripted:
            cv2.imshow("rate", np.vstack([img, bar]))
        k = getkey()
        if k in (ord("1"), ord("2"), ord("3"), ord("4")):
            ratings[key_of(r)] = chr(k)
            history.append(key_of(r))
            save()
            i += 1
        elif k == ord("b") and history:
            ratings.pop(history.pop(), None)
            save()
            i = max(0, i - 1)
        elif k == ord("s"):
            i += 1
        elif k in (ord("q"), 27):
            break
    save()
    if not scripted:
        cv2.destroyAllWindows()
    dist = {g: sum(1 for v in ratings.values() if v == g) for g in "1234"}
    print(f"저장: {path} (등급 분포 {dist})")


if __name__ == "__main__":
    main()
