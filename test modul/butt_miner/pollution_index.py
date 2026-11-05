"""
pollution_index.py - C2: 후보로부터 '오염 지수 v2'를 계산한다(개수 대신 면적 피복률, 격자 점유율, 방치 시간 가중) + 사람 등급과의 상관 비교 + 영상별 히트맵

  python pollution_index.py --batch runs/batch_c1 --meta runs/meta/meta.csv --out runs/c2 --min-support 0.3 --ratings "runs/ratings/ratings*.csv"

지표(영상 하나 = 장면 하나, 후보는 영상 끝까지 남아 있다고 가정):
  n          후보 수(기준: 개수 기반)
  cover_end  영상 끝에서 후보 상자들의 합집합 면적 / 화면 면적        (면적 피복률)
  grid_occ   화면을 격자(기본 16x9)로 나눠 후보가 닿은 칸의 비율          (사진 기반 쓰레기 지수의 격자 점유율 방식)
  cover_mean 시간 평균 피복률 = ∫ 피복률(t) dt / 영상 길이              (방치 시간 가중: 일찍 생겨 오래 남을수록 큼)
  combo      100 × (0.5·grid_occ + 0.5·min(1, cover_end / 기준)),  기준 = 전체 영상 cover_end 의 95번째 백분위수(자료에서 정한 값, 임시)
--ratings 가 있으면 각 지표와 사람이 매긴 등급(1~4)의 스피어만 상관계수를 표로 보여 준다(여러 평가자 파일이면 평균).
※ 후보에는 사람 검수나 정답이 쓰이지 않는다. end_support 가 있으면 --min-support 로 일시적 후보를 거른다(add_end_support.py).
"""
import argparse
import csv
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import read_csv  # noqa: E402


def ranks(x):
    x = np.asarray(x, float)
    o = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[o[j + 1]] == x[o[i]]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def load_video(d, min_support, size_default):
    cands, side = [], {}
    sp = d / "end_support.json"
    if sp.exists():
        try:
            side = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            side = {}
    W, H = size_default
    for p in sorted((d / "candidates").glob("ID*/meta.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        W, H = (m.get("resolution") or [W, H])[:2]
        sup = m.get("end_support", side.get(p.parent.name))
        if min_support > 0 and sup is not None and sup < min_support:
            continue
        cands.append({"bbox": m["bbox_xywh"], "t0": float(m.get("first_seen_sec", m.get("detected_at_sec", 0)) or 0), "frame": p.parent / "frame.jpg",
                      "area": m.get("area_px", 0)})
    return cands, (int(W), int(H))


def union_area(boxes, W, H, sc=0.25):
    if not boxes:
        return 0.0
    m = np.zeros((max(1, int(H * sc)), max(1, int(W * sc))), np.uint8)
    for x, y, w, h in boxes:
        m[int(max(0, y) * sc):int(min(H, y + h) * sc) + 1, int(max(0, x) * sc):int(min(W, x + w) * sc) + 1] = 1
    return float(m.sum()) / m.size


def grid_occ(boxes, W, H, gx=16, gy=9):
    occ = np.zeros((gy, gx), bool)
    for x, y, w, h in boxes:
        c0, c1 = int(max(0, x) / W * gx), int(min(W - 1, x + w) / W * gx)
        r0, r1 = int(max(0, y) / H * gy), int(min(H - 1, y + h) / H * gy)
        occ[r0:r1 + 1, c0:c1 + 1] = True
    return float(occ.mean())


def metrics(cands, W, H, length, gx, gy):
    boxes = [c["bbox"] for c in cands]
    cov_end = union_area(boxes, W, H)
    order = sorted(cands, key=lambda c: c["t0"])
    integ, prev_t, cur = 0.0, 0.0, []
    for c in order:
        t = min(max(c["t0"], 0.0), length)
        integ += union_area(cur, W, H) * (t - prev_t)
        prev_t = t
        cur.append(c["bbox"])
    integ += union_area(cur, W, H) * (length - prev_t)
    return {"n": len(cands), "cover_end": cov_end, "grid_occ": grid_occ(boxes, W, H, gx, gy), "cover_mean": integ / length if length > 0 else 0.0,
            "load_cover_sec": integ}


def heat(rows_by_site, out, sc=0.25):
    """영상별 히트맵: 각 영상 자신의 배경 사진(후보 frame.jpg) 위에 그 영상의 후보 위치만 겹친다.
    (장소 단위로 여러 영상을 한 장에 합치면 서로 다른 장면의 후보가 한 배경에 섞여 위치가 맞지 않았음 - 영상별로 그려야 함)"""
    n_img = 0
    for site, vids in rows_by_site.items():
        for v in vids:
            if not v["cands"]:
                continue
            W, H = v["size"]
            acc = np.zeros((int(H * sc), int(W * sc)), np.float32)
            for c in v["cands"]:
                x, y, w, h = c["bbox"]
                acc[int(max(0, y) * sc):int(min(H, y + h) * sc) + 1, int(max(0, x) * sc):int(min(W, x + w) * sc) + 1] += 1.0
            bg = None
            for c in sorted(v["cands"], key=lambda c: -c["area"]):
                if c["frame"].exists():
                    bg = cv2.imread(str(c["frame"]))
                    break
            if bg is None or acc.max() <= 0:
                continue
            bg = cv2.resize(bg, (W, H))
            hm = cv2.resize(cv2.GaussianBlur(acc, (0, 0), 3), (W, H))
            hm = (255 * (hm / hm.max())).astype(np.uint8)
            col = cv2.applyColorMap(hm, cv2.COLORMAP_JET)
            alpha = (hm.astype(np.float32) / 255.0 * 0.7)[..., None] * (hm > 15).astype(np.float32)[..., None]
            img = (bg * (1.0 - alpha) + col * alpha).astype(np.uint8)
            cv2.putText(img, f"{v['key']} (combo: {v['combo']:.1f}, n={v['n']})", (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * W / 1920, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imwrite(str(out / f"heatmap_{v['key']}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            n_img += 1
    return n_img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--out", default="runs/c2")
    p.add_argument("--min-support", type=float, default=0.0)
    p.add_argument("--ratings", default="", help="사람 등급 CSV(key,rating). 와일드카드 가능, 여러 파일이면 평균")
    p.add_argument("--gt", action="append", default=[], help="정답 상자 json(gt_tools 형식). 정답 상자로 계산한 '이상적인 지수'와 사람 등급의 상관도 함께 보여 줌(후보 노이즈 때문인지 가려내는 진단용)")
    p.add_argument("--grid", default="16x9")
    p.add_argument("--sites", default="", help="이 장소만 계산(쉼표 구분)")
    a = p.parse_args()
    gx, gy = (int(x) for x in a.grid.lower().split("x"))
    meta = {Path(r["file"].split("::")[-1]).stem: r for r in read_csv(a.meta)}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    batch, out = Path(a.batch), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    vids = []
    for d in sorted(batch.iterdir()):
        if not (d.is_dir() and (d / "pipeline_summary.json").exists() and d.name in meta):
            continue
        r = meta[d.name]
        if sites and r["site"] not in sites:
            continue
        length = float(json.loads((d / "pipeline_summary.json").read_text(encoding="utf-8")).get("length_sec") or 120)
        cands, size = load_video(d, a.min_support, (1920, 1080))
        m = metrics(cands, size[0], size[1], length, gx, gy)
        vids.append({"key": d.name, "site": r["site"], "weather": r["weather"], "tod": r["tod"], "scenario_no": r.get("scenario_no", ""),
                     "scenario": r.get("scenario", ""), "length": length, "size": size, "cands": cands, **m})
    if not vids:
        sys.exit("계산할 영상이 없습니다(batch 폴더, meta.csv 확인).")
    ref = float(np.percentile([v["cover_end"] for v in vids], 95)) or 1e-9
    for v in vids:
        v["combo"] = 100 * (0.5 * v["grid_occ"] + 0.5 * min(1.0, v["cover_end"] / ref))
    cols = ["key", "site", "weather", "tod", "scenario_no", "scenario", "n", "cover_end", "grid_occ", "cover_mean", "load_cover_sec", "combo"]
    with open(out / "pollution_v2.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=cols)
        wr.writeheader()
        for v in vids:
            wr.writerow({k: (round(v[k], 4) if isinstance(v[k], float) else v[k]) for k in cols})
    print(f"영상 {len(vids)}개, 후보 필터: end_support >= {a.min_support}, 격자 {gx}x{gy}, 피복률 기준(95%) {ref * 100:.2f}%")
    for name in ("n", "cover_end", "grid_occ", "cover_mean", "combo"):
        x = np.array([v[name] for v in vids])
        print(f"  {name:11} 평균 {x.mean():8.3f}  중앙값 {np.median(x):8.3f}  최대 {x.max():8.3f}")

    by = defaultdict(list)
    for v in vids:
        by[(v["scenario_no"], v["scenario"][:34])].append(v)
    print("\n[시나리오별] 영상 / 평균 후보 수 / 평균 피복률% / 평균 격자 점유율%  (시나리오 이름의 봉투 개수와 비교해 보세요)")
    for (no, name), vs in sorted(by.items(), key=lambda kv: float(np.mean([v["cover_end"] for v in kv[1]])), reverse=True)[:12]:
        print(f"  {no:>3} {name:34} {len(vs):2} / {np.mean([v['n'] for v in vs]):5.1f} / {np.mean([v['cover_end'] for v in vs]) * 100:5.2f} / {np.mean([v['grid_occ'] for v in vs]) * 100:5.1f}")

    if a.ratings:
        files = sorted(glob.glob(a.ratings))
        per = []
        for f in files:
            d = {}
            for r in read_csv(f):
                try:
                    d[r["key"]] = float(r["rating"])
                except (KeyError, ValueError):
                    pass
            per.append(d)
        rt = defaultdict(list)
        for d in per:
            for k, v in d.items():
                rt[k].append(v)
        both = [(v, float(np.mean(rt[v["key"]]))) for v in vids if v["key"] in rt]
        n_b = len(both)
        print(f"\n[사람 등급(1~4)과의 스피어만 상관] 평가자 파일 {len(files)}개, 등급이 있는 영상 {n_b}개")
        if n_b >= 5:
            rr = [r for _, r in both]
            dist = {g: sum(1 for x in rr if round(x) == g) for g in (1, 2, 3, 4)}
            print(f"  등급 분포(평균을 반올림): {dist}  <- 한두 등급에 몰려 있으면 상관이 낮게 나오기 쉬움")
            crit = 1.96 / np.sqrt(max(n_b - 1, 1))
            print(f"  참고: 영상 {n_b}개에서 |rho| 가 약 {crit:.2f} 보다 작으면 우연과 구분되지 않음(대략적인 기준)")
            for name in ("n", "cover_end", "grid_occ", "cover_mean", "combo"):
                rho = spearman([v[name] for v, _ in both], rr)
                print(f"  {name:11} rho = {rho:6.3f}  {'(우연 수준)' if abs(rho) < crit else ''}")
            if len(per) >= 2:
                common = sorted(set(per[0]) & set(per[1]))
                if len(common) >= 5:
                    print(f"  평가자 간 일치도(스피어만) {spearman([per[0][k] for k in common], [per[1][k] for k in common]):.3f} (공통 영상 {len(common)}개)")
        else:
            print("  등급이 5개 미만이라 계산하지 않았습니다(rate_scenes.py 로 더 매기세요).")
        if a.gt and n_b >= 5:
            gts = {}
            for gp in a.gt:
                gts.update(json.loads(Path(gp).read_text(encoding="utf-8")))
            orc = []
            for v, r in both:
                g = gts.get(v["key"])
                if g is None:
                    continue
                boxes = g["boxes"]
                W, H = g["size"]
                orc.append((len(boxes), union_area(boxes, W, H), grid_occ(boxes, W, H, gx, gy), r, v["n"], v["cover_end"]))
            if len(orc) >= 5:
                o = np.array(orc)
                print(f"\n[진단] 정답 상자로 계산한 '이상적인 지수'와 사람 등급의 상관 (정답이 있는 영상 {len(orc)}개)")
                print(f"  정답 봉투 수  rho = {spearman(o[:, 0], o[:, 3]):6.3f}")
                print(f"  정답 피복률   rho = {spearman(o[:, 1], o[:, 3]):6.3f}")
                print(f"  정답 격자 점유 rho = {spearman(o[:, 2], o[:, 3]):6.3f}")
                print(f"  (같은 영상에서) 후보 기반: n rho = {spearman(o[:, 4], o[:, 3]):6.3f}, cover_end rho = {spearman(o[:, 5], o[:, 3]):6.3f}")
                print("  -> 정답 기반이 높고 후보 기반이 낮으면 '후보 노이즈'가 원인, 정답 기반도 낮으면 '등급 기준이나 평가'가 원인")
    by_site = defaultdict(list)
    for v in vids:
        by_site[v["site"]].append(v)
    n_img = heat(by_site, out)
    print("\n[장소별 평균] 영상 / 평균 후보 수 / 평균 격자 점유율% / 평균 combo")
    for site, vs in sorted(by_site.items(), key=lambda kv: -len(kv[1])):
        print(f"  {site:6} {len(vs):3} / {np.mean([v['n'] for v in vs]):5.1f} / {np.mean([v['grid_occ'] for v in vs]) * 100:5.1f} / {np.mean([v['combo'] for v in vs]):5.1f}")
    print("  ※ 이 데이터는 연출된 투기 영상이라 장소 간 차이는 그 장소의 실제 오염 정도가 아니라 후보 노이즈와 시나리오 구성 차이일 수 있음.")
    print(f"\n저장: {out / 'pollution_v2.csv'}, 영상별 히트맵 {n_img}장 ({out}/heatmap_<영상키>.jpg)")


if __name__ == "__main__":
    main()
