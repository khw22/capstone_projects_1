r"""
candidate_classifier.py - C3: 후보가 '봉투'일 확률을 예측하는 가벼운 분류기(데이터 엔진의 'AI 추천 라벨')

두 단계로 쓴다.  (test modul\butt_miner\ 폴더에 두고 실행)

  1) 특징 만들기 (후보마다 숫자 특징 + 정답 라벨):
     python candidate_classifier.py features --batch runs/batch_a50 --meta runs/meta/meta.csv --gt runs/gt/gt_events.json --gt-manual runs/gt_manual/gt_events.json --out runs/c3/features.csv

  2) 장소 하나씩 빼고 학습 -> 독립 정답(gt_manual)으로 평가 + 최종 모델 저장:
     python candidate_classifier.py evaluate --features runs/c3/features.csv --gt-manual runs/gt_manual/gt_events.json --out runs/c3

평가 규칙(순환 평가 방지):
- 학습 라벨은 runs/gt(처음-끝 차이로 제안한 정답), 평가 라벨은 runs/gt_manual(사람이 직접 그린 정답).
- gt_manual 에 들어 있는 영상은 학습에서 완전히 뺀다.
- 평가는 '장소 하나를 통째로 빼고' 학습한 모델로 그 장소의 gt_manual 영상을 채점한다(다른 장소에서도 통하는지 확인).
- 비교 기준(기준선, end_support 0.3 이상)도 같은 영상·같은 방식으로 같이 계산한다.
- 후보와 정답이 닿는지는 gt_tools.touches 기준이다.
주의: end_support 와 runs/gt 는 둘 다 '처음-끝 차이'에서 나와 서로 닮아 있다. 그래서 --drop end_support 로 뺀 결과도 같이 확인할 것.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_tools  # noqa: E402
from video_source import read_csv  # noqa: E402

BASE = ["area", "log_area", "w", "h", "aspect", "static_sec", "first_seen_sec", "detected_at_sec", "resumed",
        "cx_rel", "cy_rel", "end_support", "n_cands", "n_near100", "n_cell120", "area_rel_med", "area_rank", "time_rank"]
CROP = ["gray_in", "gray_std", "ring_gray", "contrast", "edge_density", "sat_in"]
META_COLS = ["key", "id", "site", "in_gt", "in_manual", "label_gt", "label_manual", "bx", "by", "bw", "bh"]


# ------------------------------------------------------------------ 특징
def load_cands(vdir):
    side = {}
    sp = vdir / "end_support.json"
    if sp.exists():
        try:
            side = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            side = {}
    out = []
    for p in sorted((vdir / "candidates").glob("ID*/meta.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append((p.parent, m, m.get("end_support", side.get(p.parent.name))))
    return out


def crop_feats(frame_path, box):
    import cv2
    nan = float("nan")
    img = cv2.imread(str(frame_path))
    if img is None:
        return {k: nan for k in CROP}
    x, y, w, h = box
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return {k: nan for k in CROP}
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    pad = max(12, w, h)
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    inner = g[y0:y1, x0:x1].astype(np.float32)
    big = g[Y0:Y1, X0:X1].astype(np.float32)
    ring_n = big.size - inner.size
    ring = (big.sum() - inner.sum()) / ring_n if ring_n > 0 else float(inner.mean())
    edges = cv2.Canny(g[Y0:Y1, X0:X1], 60, 140)
    return {"gray_in": float(inner.mean()), "gray_std": float(inner.std()), "ring_gray": float(ring),
            "contrast": float(abs(inner.mean() - ring)), "edge_density": float((edges > 0).mean()),
            "sat_in": float(hsv[y0:y1, x0:x1, 1].mean())}


def video_rows(cands, crop):
    n = len(cands)
    boxes = [m["bbox_xywh"] for _, m, _ in cands]
    W, H = (cands[0][1].get("resolution") or [1920, 1080])[:2]
    areas = np.array([b[2] * b[3] for b in boxes], float)
    cx = np.array([b[0] + b[2] / 2 for b in boxes])
    cy = np.array([b[1] + b[3] / 2 for b in boxes])
    times = np.array([m.get("detected_at_sec", 0) for _, m, _ in cands], float)
    med = float(np.median(areas)) or 1.0
    ar_rank = areas.argsort().argsort() / max(1, n - 1)
    t_rank = times.argsort().argsort() / max(1, n - 1)
    cells = defaultdict(int)
    for b in boxes:
        cells[(int(b[0] // 120), int(b[1] // 120))] += 1
    out = []
    for i, ((d, m, sup), b) in enumerate(zip(cands, boxes)):
        dist = np.hypot(cx - cx[i], cy - cy[i])
        f = {"area": areas[i], "log_area": float(np.log1p(areas[i])), "w": b[2], "h": b[3], "aspect": b[2] / max(1, b[3]),
             "static_sec": m.get("static_sec", 0), "first_seen_sec": m.get("first_seen_sec", 0),
             "detected_at_sec": times[i], "resumed": m.get("resumed_count", 0),
             "cx_rel": cx[i] / W, "cy_rel": cy[i] / H, "end_support": -1.0 if sup is None else sup,
             "n_cands": n, "n_near100": int((dist < 100).sum()) - 1, "n_cell120": cells[(int(b[0] // 120), int(b[1] // 120))],
             "area_rel_med": areas[i] / med, "area_rank": float(ar_rank[i]), "time_rank": float(t_rank[i])}
        if crop:
            f.update(crop_feats(d / "frame.jpg", b))
        out.append((d.name, b, f))
    return out


def cmd_features(a):
    meta = {gt_tools.key_of(r["file"]): r for r in read_csv(a.meta)}
    gt = json.loads(Path(a.gt).read_text(encoding="utf-8"))
    man = json.loads(Path(a.gt_manual).read_text(encoding="utf-8")) if a.gt_manual else {}
    batch = Path(a.batch)
    cols = META_COLS + BASE + (CROP if not a.no_crop else [])
    rows, nvid, nosup = [], 0, 0
    for vdir in sorted(d for d in batch.iterdir() if d.is_dir()):
        k = vdir.name
        if k not in meta or not (vdir / "candidates").exists():
            continue
        cands = load_cands(vdir)
        nvid += 1
        if not cands:
            continue
        for cid, b, f in video_rows(cands, not a.no_crop):
            nosup += f["end_support"] < 0
            row = {"key": k, "id": cid, "site": meta[k]["site"], "in_gt": int(k in gt), "in_manual": int(k in man),
                   "label_gt": "", "label_manual": "", "bx": b[0], "by": b[1], "bw": b[2], "bh": b[3]}
            if k in gt:
                row["label_gt"] = int(any(gt_tools.touches(b, gb) for gb in gt[k]["boxes"]))
            if k in man:
                row["label_manual"] = int(any(gt_tools.touches(b, gb) for gb in man[k]["boxes"]))
            row.update(f)
            rows.append(row)
        if nvid % 10 == 0:
            print(f"  영상 {nvid}개 처리...")
    if not rows:
        sys.exit("후보가 없습니다. --batch 폴더(영상별 폴더 안에 candidates)를 확인하세요.")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    lg = [r for r in rows if r["label_gt"] != ""]
    print(f"저장: {out}  (영상 {nvid}개, 후보 {len(rows)}개)")
    print(f"  runs/gt 라벨이 있는 후보 {len(lg)}개 중 봉투 {sum(r['label_gt'] for r in lg)}개 / gt_manual 영상의 후보 {sum(r['in_manual'] for r in rows)}개")
    if nosup:
        print(f"[주의] end_support 가 없는 후보 {nosup}개(-1 로 기록). add_end_support.py 를 먼저 실행했는지 확인하세요.")


# ------------------------------------------------------------------ 평가
def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def make_model(kind):
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        sys.exit("scikit-learn 이 필요합니다.  명령 프롬프트에서:  pip install scikit-learn")
    if kind == "gb":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, class_weight="balanced", random_state=0)
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, class_weight="balanced", max_iter=3000))


def cmd_evaluate(a):
    with open(a.features, newline="", encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
    man = json.loads(Path(a.gt_manual).read_text(encoding="utf-8"))
    drop = {x.strip() for x in a.drop.split(",") if x.strip()}
    if a.causal:                                           # 후보가 생긴 순간에 알 수 있는 특징만 쓴다(영상 전체·끝 장면 정보 제외)
        drop |= {"end_support", "n_cands", "n_near100", "n_cell120", "area_rel_med", "area_rank", "time_rank"}
    cols = [c for c in BASE + CROP if c in rows[0] and c not in drop]
    X = np.nan_to_num(np.array([[fnum(r[c]) for c in cols] for r in rows]), nan=0.0)
    site = np.array([r["site"] for r in rows])
    key = np.array([r["key"] for r in rows])
    in_man = np.array([int(r["in_manual"]) for r in rows]) == 1
    lab_gt = np.array([-1 if r["label_gt"] == "" else int(r["label_gt"]) for r in rows])
    lab_m = np.array([-1 if r["label_manual"] == "" else int(r["label_manual"]) for r in rows])
    box = [[int(r["bx"]), int(r["by"]), int(r["bw"]), int(r["bh"])] for r in rows]
    sup = X[:, cols.index("end_support")] if "end_support" in cols else np.array([fnum(r["end_support"]) for r in rows])
    by_key = defaultdict(list)
    for i, k in enumerate(key):
        by_key[k].append(i)
    ev_keys = [k for k, g in man.items() if g["boxes"] and k in by_key]
    ev_site = {k: man[k]["site"] for k in ev_keys}
    n_boxes = sum(len(man[k]["boxes"]) for k in ev_keys)
    print(f"특징 {len(cols)}개: {cols}" + (f"  (뺀 특징: {sorted(drop)})" if drop else ""))
    print(f"평가 영상(gt_manual, 봉투 있음) {len(ev_keys)}개, 봉투 {n_boxes}개, 장소 {dict(sorted(defaultdict(int, {s: sum(1 for k in ev_keys if ev_site[k] == s) for s in set(ev_site.values())}).items()))}")

    P = np.full(len(rows), np.nan)
    for s in sorted(set(ev_site.values())):
        tr = (site != s) & (~in_man) & (lab_gt >= 0)
        te = np.array([k in ev_site and ev_site[k] == s for k in key])
        npos = int(lab_gt[tr].sum())
        if npos < 5 or te.sum() == 0:
            print(f"[건너뜀] 장소 {s}: 학습 양성 {npos}개(5개 미만)")
            continue
        m = make_model(a.model).fit(X[tr], lab_gt[tr])
        P[te] = m.predict_proba(X[te])[:, 1]
        print(f"  장소 {s} 제외 학습: 후보 {int(tr.sum())}개(봉투 {npos}개) -> 시험 후보 {int(te.sum())}개")
    ok_keys = [k for k in ev_keys if not np.isnan(P[by_key[k]]).any()]
    if len(ok_keys) < len(ev_keys):
        print(f"[참고] 예측하지 못한 영상 {len(ev_keys) - len(ok_keys)}개는 모든 표에서 뺐습니다.")
    ev_keys = ok_keys
    n_boxes = sum(len(man[k]["boxes"]) for k in ev_keys)

    def run(keep_fn, keys):
        C = CH = GH = G = DET = 0
        for k in keys:
            idx = by_key[k]
            kept = keep_fn(idx)
            hits = sum(1 for gb in man[k]["boxes"] if any(gt_tools.touches(box[i], gb) for i in kept))
            C += len(kept)
            CH += sum(1 for i in kept if lab_m[i] == 1)
            GH += hits
            G += len(man[k]["boxes"])
            DET += hits > 0
        n = max(1, len(keys))
        return C / n, (CH / C * 100 if C else 0.0), (GH / G * 100 if G else 0.0), GH, G, DET / n * 100

    def topk(K):
        return lambda idx: sorted(idx, key=lambda i: -P[i])[:K]

    # 대조군: 학습 없이 '면적 큰 순', 'end_support 높은 순(같으면 면적 큰 순)'으로 상위 K개만 남기는 단순 규칙.
    # 분류기가 이 단순 규칙보다 나아야 '학습의 효과'라고 말할 수 있다.
    area_arr = np.array([float(r["area"]) for r in rows])
    sup_score = sup + area_arr * 1e-9

    def topk_by(score, K):
        return lambda idx: sorted(idx, key=lambda i: -score[i])[:K]

    settings = [("전체 후보(기준선)", lambda idx: list(idx)),
                ("end_support >= 0.3 [오프라인]", lambda idx: [i for i in idx if sup[i] >= 0.3])]
    for K in (5, 10, 15, 20, 30):
        settings.append((f"분류기 상위 {K}개 [오프라인]", topk(K)))
        settings.append((f"  (대조)면적 큰 순 {K}개 [오프라인]", topk_by(area_arr, K)))
        settings.append((f"  (대조)end_support 순 {K}개 [오프라인]", topk_by(sup_score, K)))
    # 실시간 가능한 규칙: 후보 하나만 보고 바로 정하는 기준(면적 하한, 확률 하한). 영상 전체나 끝 장면이 필요 없다.
    rt_note = "" if a.causal else " (※--causal 없이는 오프라인 특징 포함)"
    for T in (100, 200, 300, 500, 800, 1200, 2000):
        settings.append((f"면적 >= {T}px² [실시간 가능]", (lambda T: lambda idx: [i for i in idx if area_arr[i] >= T])(T)))
    for t in (0.1, 0.2, 0.3, 0.5, 0.7, 0.9):
        settings.append((f"분류기 확률 >= {t} [실시간{'가능' if a.causal else '아님'}]", (lambda t: lambda idx: [i for i in idx if P[i] >= t])(t)))
    print(f"\n[장소 제외 교차검증, 독립 정답 gt_manual] 영상 {len(ev_keys)}개, 봉투 {n_boxes}개 | 모델 {a.model}{rt_note}")
    print(f"{'설정':40} {'영상당 후보':>10} {'정밀도':>8} {'봉투 재현율':>14} {'사건 검출률':>10}")
    for name, fn in settings:
        c, pr, rc, gh, g, det = run(fn, ev_keys)
        print(f"{name:40} {c:10.1f} {pr:7.1f}% {rc:6.0f}% ({gh}/{g}) {det:9.0f}%")
    print("\n※ 봉투 30개 안팎이라 재현율 1개 차이가 약 3%p 입니다. 작은 차이로 결론 내리지 마세요.")
    print("※ [오프라인]은 영상 전체(또는 끝 장면)를 본 뒤 고르는 방식, [실시간 가능]은 후보가 생긴 순간의 정보만 쓰는 방식입니다.")
    print("※ 여러 기준값을 한꺼번에 비교한 표입니다. 이 중 가장 좋은 줄만 골라 성능이라고 쓰면 과장입니다(새 영상으로 다시 확인 필요).")

    K = a.k_site
    print(f"\n[장소별, 분류기 상위 {K}개/영상 vs end_support>=0.3] 장소 / 영상 / 후보 / 정밀도 / 재현율")
    for s in sorted(set(ev_site[k] for k in ev_keys)):
        ks = [k for k in ev_keys if ev_site[k] == s]
        for name, fn in ((f"분류기{K}", topk(K)), ("end_sup0.3", settings[1][1])):
            c, pr, rc, gh, g, det = run(fn, ks)
            print(f"  {s:5} {len(ks):2}영상 {name:11} {c:5.1f} {pr:6.1f}% {rc:5.0f}% ({gh}/{g})")

    if a.show_misses:
        print(f"\n[놓친 봉투: 분류기 상위 {K}개 기준] 장소 / 영상 / 정답 크기 / 그 봉투에 닿는 후보의 (순위, 면적, end_support, 확률)")
        for k in ev_keys:
            idx = by_key[k]
            order = sorted(idx, key=lambda i: -P[i])
            rank = {i: r + 1 for r, i in enumerate(order)}
            for gb in man[k]["boxes"]:
                tch = [i for i in idx if gt_tools.touches(box[i], gb)]
                if any(rank[i] <= K for i in tch):
                    continue
                info = ", ".join(f"({rank[i]}위, {box[i][2] * box[i][3]}px², sup {sup[i]:.2f}, p {P[i]:.2f})"
                                 for i in sorted(tch, key=lambda i: rank[i])[:3]) or "닿는 후보가 아예 없음(기준선도 놓친 봉투)"
                print(f"  {ev_site[k]:5} {k}  정답 {gb[2]}x{gb[3]}px: {info}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "predictions_loso.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.writer(fp)
        wr.writerow(["key", "id", "site", "p_bag", "label_manual"])
        for i, r in enumerate(rows):
            if not np.isnan(P[i]):
                wr.writerow([r["key"], r["id"], r["site"], round(float(P[i]), 4), r["label_manual"]])
    tr = (~in_man) & (lab_gt >= 0)
    final = make_model(a.model).fit(X[tr], lab_gt[tr])
    try:
        import joblib
        joblib.dump({"model": final, "features": cols, "kind": a.model}, out / "model.joblib")
        print(f"\n최종 모델(gt_manual 영상 제외 전체 학습) 저장: {out / 'model.joblib'}")
    except Exception as e:
        print(f"[참고] 모델 저장 실패: {e}")
    if a.model == "lr":
        coef = final[-1].coef_[0]
        print("\n[로지스틱 계수: 양수면 '봉투일수록 큼'] (표준화된 값 기준, 절댓값 큰 순)")
        for c, w in sorted(zip(cols, coef), key=lambda t: -abs(t[1]))[:10]:
            print(f"  {c:16} {w:+.2f}")
    print(f"예측 저장: {out / 'predictions_loso.csv'}")


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("features")
    f.add_argument("--batch", required=True)
    f.add_argument("--meta", required=True)
    f.add_argument("--gt", required=True, help="학습 라벨: runs/gt/gt_events.json")
    f.add_argument("--gt-manual", default="", help="평가 라벨: runs/gt_manual/gt_events.json")
    f.add_argument("--out", default="runs/c3/features.csv")
    f.add_argument("--no-crop", action="store_true", help="frame.jpg 에서 뽑는 밝기·가장자리 특징을 쓰지 않음(빠름)")
    e = sp.add_parser("evaluate")
    e.add_argument("--features", required=True)
    e.add_argument("--gt-manual", required=True)
    e.add_argument("--out", default="runs/c3")
    e.add_argument("--model", default="lr", choices=["lr", "gb"], help="lr=로지스틱(기본), gb=작은 트리 모델")
    e.add_argument("--drop", default="", help="뺄 특징(쉼표 구분). 예: end_support")
    e.add_argument("--k-site", type=int, default=15)
    e.add_argument("--causal", action="store_true", help="실시간 가능한 특징만 사용(end_support, 영상 전체 후보 수·순위 등을 뺌)")
    e.add_argument("--show-misses", action="store_true", help="--k-site 개 안에 못 든 봉투를 하나씩 보여 줌(왜 놓쳤는지 확인용)")
    a = p.parse_args()
    {"features": cmd_features, "evaluate": cmd_evaluate}[a.cmd](a)


if __name__ == "__main__":
    main()
