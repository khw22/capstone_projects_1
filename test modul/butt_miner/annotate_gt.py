"""
annotate_gt.py - 영상 끝 장면에 사람이 직접 봉투 위치를 그려서 '독립적인 정답'을 만든다 (자동 제안과 무관한 정답, 일부 영상만)

  python annotate_gt.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --out runs/gt_manual --n 20

  각 영상에서: 마우스로 봉투를 드래그해 ENTER(또는 SPACE) -> 다음 봉투 ... 다 끝나면 ESC. 봉투가 없으면 바로 ESC.
- 이유: runs/gt 의 정답은 'gt_tools propose'(처음-끝 장면 차이)에서 나왔고, end_support 도 같은 차이로 계산해서 둘을 비교하면 순환입니다.
  이 도구로 사람이 직접 그린 정답은 그 차이와 무관하므로 end_support 효과를 공정하게 평가할 수 있습니다.
- 장소가 고르게 섞이도록 --n 개를 뽑고, 결과는 runs/gt_manual/gt_events.json (gt_tools 와 같은 형식). 이어서 하면 이미 한 영상은 건너뜁니다.
- 평가:  python gt_tools.py evaluate --gt runs/gt_manual/gt_events.json --batch runs/batch_c1 --sweep 0,0.1,0.2,0.3,0.5
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2

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
    p.add_argument("--out", default="runs/gt_manual")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--angles", default="a")
    p.add_argument("--sites", default="")
    p.add_argument("--tmp", default=None)
    p.add_argument("--auto-test", action="store_true", help=argparse.SUPPRESS)     # 화면 없이 시험용
    a = p.parse_args()
    angles = {x.strip() for x in a.angles.split(",") if x.strip()}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    rows = [r for r in read_csv(a.meta) if r["angle"] in angles and (not sites or r["site"] in sites)]
    by_site = defaultdict(list)
    for r in rows:
        by_site[r["site"]].append(r)
    rnd = random.Random(a.seed)
    for v in by_site.values():
        rnd.shuffle(v)
    picked = []
    while len(picked) < a.n and any(by_site.values()):                           # 장소를 번갈아 가며 뽑기
        for s in sorted(by_site):
            if by_site[s] and len(picked) < a.n:
                picked.append(by_site[s].pop())
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gp = out / "gt_events.json"
    gt = json.loads(gp.read_text(encoding="utf-8")) if gp.exists() else {}
    key_of = lambda r: Path(r["file"].split("::")[-1]).stem
    todo = [r for r in picked if key_of(r) not in gt and r["file"] in items]
    print(f"그릴 영상 {len(todo)}개(이미 한 {len(gt)}개). 봉투를 드래그 -> ENTER, 다음 봉투 -> 드래그 -> ENTER, 끝나면 ESC(봉투 없으면 바로 ESC)")
    for i, r in enumerate(todo, 1):
        with open_video(items[r["file"]], a.tmp) as vp:
            f = last_frame(vp)
        if f is None:
            continue
        H, W = f.shape[:2]
        s = min(1.0, 1400 / W)
        img = cv2.resize(f, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if a.auto_test:
            boxes = [(int(0.4 * img.shape[1]), int(0.5 * img.shape[0]), 80, 50)]
        else:
            print(f"[{i}/{len(todo)}] {key_of(r)}")
            boxes = cv2.selectROIs("annotate", img, showCrosshair=False)
            boxes = [tuple(map(int, b)) for b in boxes] if len(boxes) else []
        real = [[int(x / s), int(y / s), int(w / s), int(h / s)] for x, y, w, h in boxes if w > 3 and h > 3]
        gt[key_of(r)] = {"file": r["file"], "site": r["site"], "weather": r["weather"], "tod": r["tod"], "size": [W, H], "boxes": real}
        gp.write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.auto_test:
        cv2.destroyAllWindows()
    print(f"저장: {gp} (영상 {len(gt)}개, 상자 {sum(len(g['boxes']) for g in gt.values())}개)")


if __name__ == "__main__":
    main()
