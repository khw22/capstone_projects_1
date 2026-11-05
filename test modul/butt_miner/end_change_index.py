"""
end_change_index.py - C2 대안 지표: 후보 검출 없이 '깨끗한 처음 장면 대비 끝 장면에서 지속적으로 달라진 면적'으로 오염 정도를 잰다

  python end_change_index.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --out runs/c2_end --ratings "runs/ratings/ratings*.csv"

생각: 사람은 '원래 비어 있던 곳에 얼마나 많은 것이 놓였는가'로 오염을 판단한다. 처음 장면(기준 배경)과 끝 장면의 차이(카메라 이동 정렬, 구역별 밝기 보정)에서
작은 조각을 지우고 남은 변화 면적의 비율을 지표로 삼는다. 후보 추출의 노이즈(정밀도 6~16%)를 거치지 않는다.
  cover_mask  남은 변화 면적 / 화면 면적        grid_mask  격자(16x9) 칸 중 변화가 5% 이상인 칸의 비율        n_comp  남은 변화 덩어리 수
※ 이 지표도 후보 기반 지표와 같은 영상으로 평가하므로, 두 가지를 모두 보고해야 한다(좋은 쪽만 고르면 안 됨). 개발 장소와 나머지 장소를 나눠 상관을 따로 보여 준다.
※ 실제 서비스에서는 '끝 장면'이 '최근 N분 평균 장면', '처음 장면'이 '깨끗한 기준 배경(온보딩)'에 해당한다.
"""
import argparse
import csv
import glob
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_tools  # noqa: E402
from pollution_index import spearman  # noqa: E402
from video_source import list_videos, open_video, read_csv  # noqa: E402


def measure(vp, thr, min_comp, gx=16, gy=9):
    em = gt_tools.end_mask(vp, thr)
    if em is None:
        return None
    mask, (W, H), (dx, dy) = em["mask"].copy(), em["size"], em["shift"]
    m = int(0.01 * W + max(abs(dx), abs(dy))) + 2
    mask[:m, :] = 0
    mask[-m:, :] = 0
    mask[:, :m] = 0
    mask[:, -m:] = 0
    cnt, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    keep = np.zeros_like(mask)
    n = 0
    for i in range(1, cnt):
        x, y, w, h, area = st[i]
        if area >= min_comp and w * h <= 0.25 * W * H:
            keep[lab == i] = 255
            n += 1
    cell = keep.reshape(1, *keep.shape)
    gh, gw = H // gy, W // gx
    occ = sum(1 for r in range(gy) for c in range(gx) if (keep[r * gh:(r + 1) * gh, c * gw:(c + 1) * gw] > 0).mean() >= 0.05)
    return {"cover_mask": float((keep > 0).mean()), "grid_mask": occ / (gx * gy), "n_comp": n, "shift": round(float(max(abs(dx), abs(dy))), 1)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--out", default="runs/c2_end")
    p.add_argument("--ratings", default="")
    p.add_argument("--angles", default="a")
    p.add_argument("--sites", default="")
    p.add_argument("--dev-sites", default="smp", help="개발 장소(상관을 따로 보여 줌, 쉼표 구분)")
    p.add_argument("--diff-thr", type=int, default=30)
    p.add_argument("--min-comp", type=int, default=400)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--tmp", default=None)
    a = p.parse_args()
    angles = {x.strip() for x in a.angles.split(",") if x.strip()}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    rows = [r for r in read_csv(a.meta) if r["angle"] in angles and (not sites or r["site"] in sites)]
    if a.limit:
        rows = rows[:a.limit]
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = []
    for i, r in enumerate(rows, 1):
        if r["file"] not in items:
            continue
        key = Path(r["file"].split("::")[-1]).stem
        with open_video(items[r["file"]], a.tmp) as vp:
            m = measure(vp, a.diff_thr, a.min_comp)
        if m is None:
            print(f"[{i}/{len(rows)}] {key}: 프레임을 읽지 못함")
            continue
        res.append({"key": key, "site": r["site"], "weather": r["weather"], "tod": r["tod"], **m})
        print(f"[{i}/{len(rows)}] {key}  변화 면적 {m['cover_mask'] * 100:.2f}%, 격자 {m['grid_mask'] * 100:.1f}%, 덩어리 {m['n_comp']}개")
    if not res:
        sys.exit("계산된 영상이 없습니다.")
    with open(out / "end_change.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(res[0].keys()))
        w.writeheader()
        w.writerows(res)
    print(f"\n저장: {out / 'end_change.csv'} (영상 {len(res)}개)")
    if a.ratings:
        rt = defaultdict(list)
        files = sorted(glob.glob(a.ratings))
        for f in files:
            for r in read_csv(f):
                try:
                    rt[r["key"]].append(float(r["rating"]))
                except (KeyError, ValueError):
                    pass
        dev = {x.strip() for x in a.dev_sites.split(",") if x.strip()}
        print(f"\n[사람 등급과의 스피어만 상관] 평가자 파일 {len(files)}개")
        for title, sub in (("전체", res), (f"개발 장소 {sorted(dev)}", [v for v in res if v["site"] in dev]), ("나머지 장소(시험)", [v for v in res if v["site"] not in dev])):
            both = [(v, float(np.mean(rt[v["key"]]))) for v in sub if v["key"] in rt]
            if len(both) < 5:
                print(f"  {title}: 등급이 있는 영상 {len(both)}개(5개 미만이라 생략)")
                continue
            rr = [x for _, x in both]
            crit = 1.96 / np.sqrt(len(both) - 1)
            line = ", ".join(f"{k} rho={spearman([v[k] for v, _ in both], rr):.3f}" for k in ("cover_mask", "grid_mask", "n_comp"))
            print(f"  {title} (영상 {len(both)}개, 우연 수준 |rho|<{crit:.2f}): {line}")


if __name__ == "__main__":
    main()
