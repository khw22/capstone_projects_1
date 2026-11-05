"""
gt_tools.py - 정답(봉투 위치)을 '자동 제안 + 사람 확인' 방식으로 만들고, 일괄 후보 추출 결과를 채점한다

이 데이터는 영상이 끝날 때까지 봉투가 그대로 남아 있으므로, '영상 처음(빈 장면)'과 '영상 끝(봉투가 놓인 장면)'의 차이로 봉투 위치를 제안할 수 있다.
제안은 정답이 아니다(조명 변화, 사람, 수풀 흔들림도 섞임). 사람이 겹쳐 그린 이미지를 보고 맞는 상자만 남긴다.

  1) python gt_tools.py propose  --dir 영상.zip --meta runs/meta/meta.csv --out runs/gt --angles a [--limit 5]
       -> runs/gt/overlay/*.jpg (끝 장면 + 번호 붙은 상자), runs/gt/gt_confirm.csv
  2) 이미지를 보고 gt_confirm.csv 의 keep_ids 를 고친다:  all(전부) / none(봉투 없음) / 1,3 (번호만)
  3) python gt_tools.py finalize --out runs/gt                    -> runs/gt/gt_events.json
  4) python gt_tools.py evaluate --gt runs/gt/gt_events.json --batch runs/batch1
       -> 영상별·장소별·조건별 검출률(정답 봉투를 후보가 덮었는가), 정밀도(후보 중 정답에 닿은 비율)

※ 채점은 '후보 상자가 정답 상자와 겹치는가'만 본다(시각·지연은 보지 않음). 정답은 사람이 확인한 것만 의미가 있다.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import list_videos, open_video, read_csv  # noqa: E402


def key_of(file):
    return Path(file.split("::")[-1]).stem


def read_at(cap, idxs):
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            out.append(f)
    return out


def end_mask(vp, thr=30, normalize=True):
    """영상 처음(빈 장면)과 끝 장면의 차이 마스크를 만든다(카메라 이동 정렬 + 구역별 밝기 보정 + 모폴로지).
    반환: dict(mask, size, shift, end) 또는 프레임이 부족하면 None"""
    cap = cv2.VideoCapture(vp)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W, H = int(cap.get(3)), int(cap.get(4))
    k = 5
    first = read_at(cap, range(0, k))
    last = read_at(cap, range(max(k, n - k), n))
    cap.release()
    if len(first) < 2 or len(last) < 2:
        return None
    bg = np.median(np.stack(first), axis=0).astype(np.uint8)
    end = np.median(np.stack(last), axis=0).astype(np.uint8)
    g_bg, g_end = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY) for x in (bg, end))
    sc = min(1.0, 640.0 / W)
    sm = lambda g: np.float32(cv2.resize(g, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA))
    win = cv2.createHanningWindow((sm(g_bg).shape[1], sm(g_bg).shape[0]), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(sm(g_bg), sm(g_end), win)
    dx, dy = dx / sc, dy / sc
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    end_al = cv2.warpAffine(end, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    g_al = cv2.cvtColor(end_al, cv2.COLOR_BGR2GRAY)
    if normalize:                                         # 해 질 무렵처럼 밝기가 서서히 바뀌는 영상: 구역별 밝기 비율(블록 중앙값)을 맞춘 뒤 비교
        bs = 128
        gh, gw = max(1, H // bs), max(1, W // bs)
        ratio = np.ones((gh, gw), np.float32)
        for by in range(gh):
            for bx in range(gw):
                ys, xs = slice(by * H // gh, (by + 1) * H // gh), slice(bx * W // gw, (bx + 1) * W // gw)
                ratio[by, bx] = np.median((g_al[ys, xs].astype(np.float32) + 1.0) / (g_bg[ys, xs].astype(np.float32) + 1.0))
        gain = cv2.resize(ratio, (W, H), interpolation=cv2.INTER_LINEAR)
        bg_adj = np.clip(g_bg.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    else:
        bg_adj = g_bg
    d = cv2.GaussianBlur(cv2.absdiff(g_al, bg_adj), (5, 5), 0)
    mask = (d >= thr).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    return {"mask": mask, "size": (W, H), "shift": (float(dx), float(dy)), "end": end_al}


def propose_one(vp, min_area, thr, normalize=True):
    em = end_mask(vp, thr, normalize)
    if em is None:
        return None
    mask, (W, H), (dx, dy), end_al = em["mask"].copy(), em["size"], em["shift"], em["end"]
    m = int(0.01 * W + max(abs(dx), abs(dy))) + 2                       # 정렬 오차가 생기는 가장자리는 제외
    mask[:m, :] = 0
    mask[-m:, :] = 0
    mask[:, :m] = 0
    mask[:, -m:] = 0
    cnt, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    comps = []
    for i in range(1, cnt):
        x, y, w, h, a = st[i]
        if min_area <= w * h <= 0.25 * W * H:
            comps.append({"bbox_xywh": [int(x), int(y), int(w), int(h)], "area": int(w * h)})
    comps.sort(key=lambda c: -c["area"])
    comps = comps[:12]
    for i, c in enumerate(comps, 1):
        c["id"] = i
    return {"size": [W, H], "shift_px": [round(dx, 1), round(dy, 1)], "components": comps, "end": end_al}


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    ox, oy = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ox - ix) * max(0, oy - iy)
    return inter / (aw * ah + bw * bh - inter) if inter else 0.0


def touches(c, g):
    """후보 c 가 정답 g 에 닿는가: IoU 0.05 이상, 또는 한쪽 중심이 다른 쪽(10% 확대) 안에 있음"""
    if iou(c, g) >= 0.05:
        return True
    def inside(p, b):
        x, y, w, h = b
        ex, ey = 0.1 * w, 0.1 * h
        return x - ex <= p[0] <= x + w + ex and y - ey <= p[1] <= y + h + ey
    cc = (c[0] + c[2] / 2, c[1] + c[3] / 2)
    gc = (g[0] + g[2] / 2, g[1] + g[3] / 2)
    return inside(cc, g) or inside(gc, c)


def cmd_propose(a):
    rows = list(read_csv(a.meta))
    angles = {x.strip() for x in a.angles.split(",") if x.strip()}
    sites = {x.strip() for x in a.sites.split(",") if x.strip()}
    rows = [r for r in rows if r["angle"] in angles and (not sites or r["site"] in sites)]
    if a.limit:
        rows = rows[:a.limit]
    items = {it["name"]: it for it in list_videos(a.dir)}
    out = Path(a.out)
    (out / "overlay").mkdir(parents=True, exist_ok=True)
    pj = out / "proposals.json"
    props = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
    for i, r in enumerate(rows, 1):
        k = key_of(r["file"])
        if k in props and not a.force:
            continue
        if r["file"] not in items:
            print(f"[{i}/{len(rows)}] zip 에 없음: {r['file']}")
            continue
        with open_video(items[r["file"]], a.tmp) as vp:
            res = propose_one(vp, a.min_area, a.diff_thr, not a.no_normalize)
        if res is None:
            print(f"[{i}/{len(rows)}] {k}: 프레임을 읽지 못함")
            continue
        img = res.pop("end")
        for c in res["components"]:
            x, y, w, h = c["bbox_xywh"]
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 3)
            cv2.putText(img, str(c["id"]), (x, max(30, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 255), 3, cv2.LINE_AA)
        s = 960 / img.shape[1]
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        cv2.putText(img, k, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(str(out / "overlay" / f"{k}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        res.update({"file": r["file"], "site": r["site"], "weather": r["weather"], "tod": r["tod"]})
        props[k] = res
        pj.write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
        nb = len(res["components"])
        warn = "  <-- 상한(12개)에 닿음: 조명 변화나 수풀 흔들림이 섞였을 수 있음, 이미지를 꼭 확인" if nb >= 12 else ""
        print(f"[{i}/{len(rows)}] {k}  제안 상자 {nb}개, 정렬 이동 {res['shift_px']}px{warn}")
    cp = out / "gt_confirm.csv"
    old = {}
    if cp.exists():
        old = {r["key"]: r for r in read_csv(cp)}
    with open(cp, "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.writer(fp)
        wr.writerow(["key", "n_components", "keep_ids", "note"])
        for k, v in props.items():
            prev = old.get(k)
            wr.writerow([k, len(v["components"]), prev["keep_ids"] if prev else "all", prev["note"] if prev else ""])
    print(f"\n이미지: {out / 'overlay'}\n확인표: {cp}  (keep_ids: all=전부 / none=봉투 없음 / 1,3=번호만). 고친 뒤 finalize 를 실행하세요.")


def cmd_finalize(a):
    out = Path(a.out)
    props = json.loads((out / "proposals.json").read_text(encoding="utf-8"))
    gt, none_n, total = {}, 0, 0
    for r in read_csv(out / "gt_confirm.csv"):
        k = r["key"]
        if k not in props:
            continue
        keep = r["keep_ids"].strip().lower()
        comps = props[k]["components"]
        if keep in ("", "all"):
            sel = comps
        elif keep == "none":
            sel = []
        else:
            ids = {int(x) for x in keep.replace(" ", "").split(",") if x}
            sel = [c for c in comps if c["id"] in ids]
        gt[k] = {"file": props[k]["file"], "site": props[k]["site"], "weather": props[k]["weather"], "tod": props[k]["tod"],
                 "size": props[k]["size"], "boxes": [c["bbox_xywh"] for c in sel]}
        total += len(sel)
        none_n += not sel
    (out / "gt_events.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"정답 저장: {out / 'gt_events.json'}  (영상 {len(gt)}개, 정답 상자 {total}개, 봉투 없음으로 둔 영상 {none_n}개)")


def _cands(d, min_support):
    out = []
    side = {}
    sp = d / "end_support.json"
    if sp.exists():                                       # add_end_support.py 가 따로 저장한 값(원본 후보 파일은 수정하지 않음)
        try:
            side = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            side = {}
    for p in sorted((d / "candidates").glob("ID*/meta.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sup = m.get("end_support", side.get(p.parent.name))
        if min_support > 0 and sup is not None and sup < min_support:
            continue                                      # 영상 끝에 남아 있지 않은(일시적인) 후보는 제외
        out.append(m["bbox_xywh"])
    return out


def cmd_evaluate(a):
    gt = json.loads(Path(a.gt).read_text(encoding="utf-8"))
    batch = Path(a.batch)
    cond = {}
    rp = batch / "results.csv"
    if rp.exists():
        cond = {key_of(r["file"]): r for r in read_csv(rp)}
    if a.sites:
        keep = {x.strip() for x in a.sites.split(",") if x.strip()}
        gt = {k: g for k, g in gt.items() if g["site"] in keep}
        print(f"[장소 한정] {sorted(keep)} 영상 {len(gt)}개만 채점")
    rows = []
    for k, g in gt.items():
        d = batch / k
        if not (d / "pipeline_summary.json").exists() or not g["boxes"]:
            continue
        cands = _cands(d, a.min_support)
        hit_g = sum(1 for gb in g["boxes"] if any(touches(c, gb) for c in cands))
        hit_c = sum(1 for c in cands if any(touches(c, gb) for gb in g["boxes"]))
        rows.append({"key": k, "site": g["site"], "weather": g["weather"], "tod": g["tod"], "gt": len(g["boxes"]), "gt_hit": hit_g,
                     "cands": len(cands), "cand_hit": hit_c, "detected": int(hit_g > 0), "all_found": int(hit_g == len(g["boxes"]))})
    if not rows:
        sys.exit("채점할 영상이 없습니다(정답 상자가 있는 영상이 batch 결과에 있어야 합니다).")
    with open(batch / "eval.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)

    def summ(sub):
        G, GH = sum(r["gt"] for r in sub), sum(r["gt_hit"] for r in sub)
        C, CH = sum(r["cands"] for r in sub), sum(r["cand_hit"] for r in sub)
        return (len(sub), sum(r["detected"] for r in sub) / len(sub) * 100, GH / G * 100, sum(r["all_found"] for r in sub) / len(sub) * 100,
                CH / C * 100 if C else 0.0, C / len(sub))
    if a.sweep:                                           # 끝 장면 지지도 기준을 바꿔 가며 검출률-정밀도 맞교환을 한 번에 본다
        print("[end_support 기준별] 기준 / 사건 검출률 / 봉투 재현율 / 정밀도 / 영상당 후보")
        for thr_s in [float(x) for x in a.sweep.split(",")]:
            sub = []
            for k, g in gt.items():
                d = batch / k
                if not (d / "pipeline_summary.json").exists() or not g["boxes"]:
                    continue
                cs = _cands(d, thr_s)
                hg = sum(1 for gb in g["boxes"] if any(touches(c, gb) for c in cs))
                hc = sum(1 for c in cs if any(touches(c, gb) for gb in g["boxes"]))
                sub.append({"gt": len(g["boxes"]), "gt_hit": hg, "cands": len(cs), "cand_hit": hc, "detected": int(hg > 0), "all_found": int(hg == len(g["boxes"]))})
            sx = summ(sub)
            print(f"  {thr_s:4.2f} / {sx[1]:4.0f}% / {sx[2]:4.0f}% / {sx[4]:5.1f}% / {sx[5]:5.1f}")
        print()
    n, det, rec, allf, prec, avgc = summ(rows)
    print(f"채점 영상 {n}개 | 사건 검출률 {det:.0f}% (정답 봉투를 하나라도 덮은 영상) | 봉투 재현율 {rec:.0f}% | 전부 찾은 영상 {allf:.0f}% | "
          f"정밀도 {prec:.1f}% (후보 중 정답에 닿은 비율) | 영상당 후보 {avgc:.1f}개")
    for title, key in (("장소", "site"), ("날씨(추정)", "weather"), ("시간대(추정)", "tod")):
        g = defaultdict(list)
        for r in rows:
            g[r[key]].append(r)
        print(f"\n[{title}별] 영상 / 사건 검출률 / 봉투 재현율 / 정밀도 / 영상당 후보")
        for kk in sorted(g, key=lambda x: -len(g[x])):
            s = summ(g[kk])
            print(f"  {kk:6} {s[0]:3} / {s[1]:4.0f}% / {s[2]:4.0f}% / {s[4]:5.1f}% / {s[5]:5.1f}")
    print(f"\n영상별 결과: {batch / 'eval.csv'}")


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("propose")
    a.add_argument("--dir", required=True)
    a.add_argument("--meta", required=True)
    a.add_argument("--out", default="runs/gt")
    a.add_argument("--angles", default="a")
    a.add_argument("--sites", default="")
    a.add_argument("--limit", type=int, default=0)
    a.add_argument("--tmp", default=None)
    a.add_argument("--min-area", type=int, default=400)
    a.add_argument("--diff-thr", type=int, default=30)
    a.add_argument("--force", action="store_true")
    a.add_argument("--no-normalize", action="store_true", help="구역별 밝기 보정을 끔(기본은 켬: 일몰 등 서서히 바뀌는 조명 때문에 상자가 많이 나오는 것을 줄임)")
    f = sp.add_parser("finalize")
    f.add_argument("--out", default="runs/gt")
    e = sp.add_parser("evaluate")
    e.add_argument("--gt", required=True)
    e.add_argument("--batch", required=True)
    e.add_argument("--sites", default="", help="이 장소 코드의 영상만 채점(쉼표 구분). 개발 장소에서 기준을 정하고 시험 장소에서 한 번만 평가할 때 사용")
    e.add_argument("--min-support", type=float, default=0.0, help="end_support 가 이 값 미만인 후보는 제외하고 채점(run_pipeline --verify-end 로 만든 결과에서)")
    e.add_argument("--sweep", default="", help="쉼표로 구분한 end_support 기준 목록(예: 0,0.1,0.2,0.4)을 한 번에 비교")
    args = p.parse_args()
    {"propose": cmd_propose, "finalize": cmd_finalize, "evaluate": cmd_evaluate}[args.cmd](args)


if __name__ == "__main__":
    main()
