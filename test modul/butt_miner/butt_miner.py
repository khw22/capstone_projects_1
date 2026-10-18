"""
담배꽁초 후보 자동 추출 파이프라인 (슬라이드 13~14 재구현)

[사용법]
1) 후보 추출:
   python butt_miner.py mine --video cctv1.mp4 --out runs/cctv1 --debug-video runs/cctv1/debug.mp4
   -> runs/cctv1/candidates/ID0001/{panel.jpg, frame.jpg, frame.txt, meta.json}

2) 사람이 panel.jpg를 보고 폴더를 분류:
   reviewed/good/ID0001 ...   (진짜 꽁초)
   reviewed/noise/ID0002 ...  (빛 반사, 나뭇잎 등 오탐)

3) 학습셋으로 내보내기:
   python butt_miner.py export --reviewed reviewed --dataset datasets/butt --prefix cctv1

4) 재학습:
   yolo train model=best.pt data=data.yaml epochs=50 imgsz=1280

   사람 제외 / 영역 제외 예:
   python butt_miner.py mine --video v.mp4 --out runs/v --exclude-person --ignore-region 0,0,720,110

[상태 머신]  노랑=정지 후보 / 파랑=동적 객체(제외) / 보라=가려짐(타이머 정지) / 빨강=지속 후보(최종)
"""
import argparse
import json
from math import hypot
import shutil
from collections import deque
from pathlib import Path

import cv2
import numpy as np

# BGR
COLORS = {
    "candidate": (0, 255, 255),   # 1단계 노랑
    "moving": (255, 0, 0),        # 2단계 파랑
    "occluded": (255, 0, 255),    # 3단계 보라
    "persistent": (0, 0, 255),    # 4단계 빨강
}


# ----------------------------------------------------------------- utils
def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def center_dist(a, b):
    return float(np.hypot(a[0] + a[2] / 2 - b[0] - b[2] / 2,
                          a[1] + a[3] / 2 - b[1] - b[3] / 2))


def ratio_in_box(mask, box, pad=0):
    x, y, w, h = box
    H, W = mask.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return cv2.countNonZero(mask[y0:y1, x0:x1]) / float((x1 - x0) * (y1 - y0))


class Stabilizer:
    """카메라의 작은 흔들림(평행 이동)을 첫 프레임 기준으로 보정한다 (위상 상관, 축소 영상에서 계산)."""

    def __init__(self, max_shift):
        self.max_shift, self.ref, self.win, self.scale = max_shift, None, None, 1.0
        self.last = (0.0, 0.0)

    def _prep(self, frame):
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.scale != 1.0:
            g = cv2.resize(g, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        return np.float32(g)

    def apply(self, frame):
        if self.ref is None:
            self.scale = min(1.0, 640.0 / frame.shape[1])
            self.ref = self._prep(frame)
            self.win = cv2.createHanningWindow((self.ref.shape[1], self.ref.shape[0]), cv2.CV_32F)
            return frame
        (dx, dy), _ = cv2.phaseCorrelate(self.ref, self._prep(frame), self.win)
        dx, dy = dx / self.scale, dy / self.scale
        if abs(dx) > self.max_shift or abs(dy) > self.max_shift:
            return frame            # 큰 이동은 장면 전환으로 보고 보정하지 않음
        self.last = (dx, dy)
        M = np.float32([[1, 0, -dx], [0, 1, -dy]])
        return cv2.warpAffine(frame, M, (frame.shape[1], frame.shape[0]), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)


def load_person_detector(a):
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("--exclude-person 사용에는 ultralytics가 필요합니다: pip install ultralytics")
    return YOLO(a.person_model)


def detect_persons(model, frame, a):
    """사람(person) 박스를 여백을 붙여서 반환: [(x, y, w, h), ...]"""
    r = model.predict(frame, classes=[0], conf=a.person_conf, imgsz=a.person_imgsz, verbose=False)[0]
    H, W = frame.shape[:2]
    boxes = []
    for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy():
        m = int(max(x2 - x1, y2 - y1) * a.person_margin) + 4
        bx1, by1 = max(0, int(x1) - m), max(0, int(y1) - m)
        bx2, by2 = min(W, int(x2) + m), min(H, int(y2) + m)
        boxes.append((bx1, by1, bx2 - bx1, by2 - by1))
    return boxes


def neighbor_density(diff, box):
    """후보 주변(링)에 배경 차이 픽셀이 얼마나 퍼져 있는지(0~1). 높으면 큰 물체의 일부."""
    x, y, w, h = box
    pad = max(12, 2 * max(w, h))
    H, W = diff.shape[:2]
    region = diff[max(0, y - pad):min(H, y + h + pad), max(0, x - pad):min(W, x + w + pad)]
    inner = diff[y:y + h, x:x + w]
    ring = region.size - inner.size
    if ring <= 0:
        return 0.0
    return (cv2.countNonZero(region) - cv2.countNonZero(inner)) / float(ring)


class Track:
    def __init__(self, tid, box, t):
        self.id = tid
        self.box = box
        self.first_seen = t
        self.static = 0.0      # 누적 정지 시간(가려진 동안은 증가하지 않음)
        self.occ = 0.0         # 현재 가려짐 지속 시간
        self.miss = 0.0
        self.occluded = False
        self.resumed = 0
        self.saved = False

    def state(self, persist_sec):
        if self.occluded:
            return "occluded"
        return "persistent" if self.static >= persist_sec else "candidate"


# ----------------------------------------------------------------- panel
def fit(img, size=(320, 240), interp=cv2.INTER_AREA):
    h, w = img.shape[:2]
    s = min(size[0] / w, size[1] / h)
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=interp)
    canvas = np.zeros((size[1], size[0], 3), np.uint8)
    canvas[:r.shape[0], :r.shape[1]] = r
    return canvas


def crop(img, box, ctx):
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    half = max(int(max(w, h) * ctx / 2), 16)
    H, W = img.shape[:2]
    return img[max(0, cy - half):min(H, cy + half), max(0, cx - half):min(W, cx + half)]


def label(img, text):
    cv2.putText(img, text, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def make_panel(bg, before, after, box, title):
    """3x2 비교 패널: [배경 | 등장 전 | 등장 후] / [차이 히트맵 | 12배 nearest | 스무딩]"""
    x, y, w, h = box
    marked = after.copy()
    cv2.rectangle(marked, (x - 2, y - 2), (x + w + 2, y + h + 2), (0, 0, 255), 1)
    ctx = 6
    c_bg, c_before, c_after = crop(bg, box, ctx), crop(before, box, ctx), crop(marked, box, ctx)

    d = cv2.absdiff(crop(after, box, ctx), c_bg)
    d = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    d = cv2.normalize(d, None, 0, 255, cv2.NORM_MINMAX)
    heat = cv2.applyColorMap(d, cv2.COLORMAP_JET)

    tight = crop(after, box, 1.6)
    top = np.hstack([label(fit(c_bg), "background"), label(fit(c_before), "before"),
                     label(fit(c_after), "after")])
    bot = np.hstack([label(fit(heat), "difference"),
                     label(fit(tight, interp=cv2.INTER_NEAREST), "crop nearest"),
                     label(fit(tight, interp=cv2.INTER_CUBIC), "crop smooth")])
    panel = np.vstack([top, bot])
    header = np.zeros((22, panel.shape[1], 3), np.uint8)
    cv2.putText(header, title, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return np.vstack([header, panel])


def save_candidate(tr, frame, bg, samples, out, t, fps, a):
    d = out / "candidates" / f"ID{tr.id:04d}"
    d.mkdir(parents=True, exist_ok=True)
    x, y, w, h = tr.box
    H, W = frame.shape[:2]

    target = tr.first_seen - 2.0
    before = bg
    if samples:
        st, sf = min(samples, key=lambda s: abs(s[0] - target))
        if st < tr.first_seen:
            before = sf

    title = f"ID {tr.id} state=persistent bbox=({x},{y},{w},{h}) area={w*h}px static={tr.static:.1f}s"
    cv2.imwrite(str(d / "panel.jpg"), make_panel(bg, before, frame, tr.box, title))
    cv2.imwrite(str(d / "frame.jpg"), frame)
    # YOLO 라벨 초안 (class 0 = cigarette butt). 반드시 검수 후 사용
    (d / "frame.txt").write_text(
        f"0 {(x + w / 2) / W:.6f} {(y + h / 2) / H:.6f} {w / W:.6f} {h / H:.6f}\n")
    meta = {
        "id": tr.id, "video": a.video, "bbox_xywh": [x, y, w, h],
        "area_px": w * h, "resolution": [W, H],
        "first_seen_sec": round(tr.first_seen, 2), "detected_at_sec": round(t, 2),
        "static_sec": round(tr.static, 2), "resumed_count": tr.resumed,
        "fps": fps,
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------- mine
def mine(a):
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"영상을 열 수 없습니다: {a.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps / a.proc_fps)))
    dt = step / fps
    out = Path(a.out)
    (out / "candidates").mkdir(parents=True, exist_ok=True)

    samples = deque(maxlen=a.bg_len)          # 배경 모델링용 (t, frame)
    last_sample_t, last_bg_t = -1e9, -1e9
    bg = bg_gray = prev_gray = None
    tracks, next_id = {}, 1
    k_open = np.ones((3, 3), np.uint8)
    k_close = np.ones((5, 5), np.uint8)
    k_motion = np.ones((9, 9), np.uint8)
    writer = None
    idx = -1
    stab = Stabilizer(a.stabilize_max_shift) if a.stabilize else None
    saved_pts = []
    person_model = load_person_detector(a) if a.exclude_person else None
    person_boxes, proc_n = [], 0
    ignore_regions = [tuple(int(v) for v in r.split(",")) for r in (a.ignore_region or [])]
    keep_mask = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if idx % step:
            continue
        t = idx / fps
        if stab is not None:
            frame = stab.apply(frame)
        gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        # --- 배경 모델링: 픽셀별 시간 중앙값
        if t - last_sample_t >= a.bg_interval:
            samples.append((t, frame.copy()))
            last_sample_t = t
        if len(samples) >= a.bg_min and t - last_bg_t >= a.bg_refresh:
            bg = np.median(np.stack([s[1] for s in samples]), axis=0).astype(np.uint8)
            bg_gray = cv2.GaussianBlur(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            last_bg_t = t
        if bg is None or prev_gray is None:
            prev_gray = gray
            continue

        # --- 움직임 마스크 (연속 프레임 차분)
        _, motion = cv2.threshold(cv2.absdiff(gray, prev_gray), a.motion_thr, 255, cv2.THRESH_BINARY)
        motion = cv2.dilate(motion, k_motion, iterations=2)
        prev_gray = gray

        # --- 사람 영역은 "움직임"으로 취급 -> 그 안의 후보는 파랑(제외), 기존 트랙은 보라(가려짐)
        if person_model is not None:
            if proc_n % a.person_every == 0:
                person_boxes = detect_persons(person_model, frame, a)
            for px, py, pw, ph in person_boxes:
                motion[py:py + ph, px:px + pw] = 255
        proc_n += 1

        # --- 배경 차이 영역
        _, diff = cv2.threshold(cv2.absdiff(gray, bg_gray), a.diff_thr, 255, cv2.THRESH_BINARY)
        diff = cv2.morphologyEx(diff, cv2.MORPH_OPEN, k_open)
        diff = cv2.morphologyEx(diff, cv2.MORPH_CLOSE, k_close)
        if ignore_regions:
            if keep_mask is None:
                keep_mask = np.full(diff.shape, 255, np.uint8)
                for rx, ry, rw, rh in ignore_regions:
                    keep_mask[ry:ry + rh, rx:rx + rw] = 0
            diff = cv2.bitwise_and(diff, keep_mask)
        contours, _ = cv2.findContours(diff, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        dets, moving, rejected = [], [], []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if not (a.min_area <= w * h <= a.max_area):
                continue
            if a.neighbor_ratio > 0 and neighbor_density(diff, (x, y, w, h)) > a.neighbor_ratio:
                rejected.append((x, y, w, h))    # 큰 물체(사람 등)의 일부로 판단 -> 제외
                continue
            if ratio_in_box(motion, (x, y, w, h)) > a.motion_ratio:
                moving.append((x, y, w, h))      # 2단계: 동적 객체 -> 즉시 제외
            else:
                dets.append((x, y, w, h))        # 1단계: 정지 후보

        # --- 트랙 매칭/상태 갱신
        used, dead = set(), []
        for tr in tracks.values():
            best, best_d = None, 1e9
            for j, d in enumerate(dets):
                if j in used:
                    continue
                dist = center_dist(tr.box, d)
                if (iou(tr.box, d) > 0.2 or dist < max(10, 0.7 * max(tr.box[2], tr.box[3]))) and dist < best_d:
                    best, best_d = j, dist
            if best is not None:
                used.add(best)
                tr.box = dets[best]
                tr.miss = 0.0
                if tr.occluded:                   # 가림 해제 -> 타이머 재개
                    tr.occluded = False
                    tr.occ = 0.0
                    tr.resumed += 1
                tr.static += dt
            elif ratio_in_box(motion, tr.box, pad=6) > a.occ_ratio:
                tr.occluded = True                # 3단계: 가림, 타이머 정지
                tr.occ += dt
                if tr.occ > a.occ_grace:
                    dead.append(tr.id)
            else:
                tr.miss += dt
                if tr.miss > a.miss_grace:
                    dead.append(tr.id)

            # 4단계: 지속 후보 -> 저장 (트랙당 1회)
            if not tr.saved and not tr.occluded and tr.static >= a.persist_sec:
                tr.saved = True
                cx, cy = tr.box[0] + tr.box[2] / 2, tr.box[1] + tr.box[3] / 2
                if a.dedupe_px > 0 and any(hypot(cx - px, cy - py) < a.dedupe_px and t - pt < a.dedupe_sec
                                           for px, py, pt in saved_pts):
                    continue            # 같은 자리에서 이미 저장한 후보(트랙이 끊겼다 다시 생긴 중복)
                saved_pts.append((cx, cy, t))
                save_candidate(tr, frame, bg, samples, out, t, fps, a)
                print(f"[{t:7.1f}s] 후보 저장 ID{tr.id:04d} bbox={tr.box}")

        for i in dead:
            tracks.pop(i, None)
        for j, d in enumerate(dets):
            if j not in used:
                tracks[next_id] = Track(next_id, d, t)
                next_id += 1

        # --- 디버그 영상
        if a.debug_video:
            vis = frame.copy()
            for b in rejected:      # 회색: 주변 밀도 필터로 제외
                cv2.rectangle(vis, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), (128, 128, 128), 1)
            for b in person_boxes:  # 초록: 사람 제외 영역
                cv2.rectangle(vis, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), (0, 200, 0), 1)
            for rx, ry, rw, rh in ignore_regions:  # 흰색: 수동 제외 영역
                cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), (255, 255, 255), 1)
            for b in moving:
                cv2.rectangle(vis, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), COLORS["moving"], 1)
            for tr in tracks.values():
                s = tr.state(a.persist_sec)
                x, y, w, h = tr.box
                cv2.rectangle(vis, (x, y), (x + w, y + h), COLORS[s], 2)
                cv2.putText(vis, f"ID{tr.id} {s} {tr.static:.0f}s", (x, max(12, y - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLORS[s], 1, cv2.LINE_AA)
            if writer is None:
                Path(a.debug_video).parent.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(a.debug_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                         a.proc_fps, (vis.shape[1], vis.shape[0]))
            writer.write(vis)

    cap.release()
    if writer:
        writer.release()
    print("완료. 결과:", out / "candidates")


# ----------------------------------------------------------------- export
def scale_label(text, scale):
    """YOLO 라벨 박스를 중심 기준으로 scale 배 확대한다(이미지 경계 안으로 제한). scale=1 이면 그대로."""
    if scale == 1.0:
        return text
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        xc, yc, w, h = (float(v) for v in p[1:5])
        w2, h2 = min(1.0, w * scale), min(1.0, h * scale)
        x1, x2 = max(0.0, xc - w2 / 2), min(1.0, xc + w2 / 2)
        y1, y2 = max(0.0, yc - h2 / 2), min(1.0, yc + h2 / 2)
        out.append(f"{p[0]} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
    return "\n".join(out) + "\n"


def set_class(text, cls):
    """YOLO 라벨의 클래스 번호를 cls 로 바꾼다."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 5:
            out.append(" ".join([str(cls)] + p[1:5]))
    return "\n".join(out) + "\n"


def write_data_yaml(ds, names, split_dirs, yaml_path=None):
    ypath = Path(ds) / "data.yaml"
    if ypath.exists():
        print(f"[알림] data.yaml 이 이미 있어 덮어쓰지 않았습니다: {ypath}")
        return
    lines = [f"path: {yaml_path or Path(ds).resolve().as_posix()}", "train: images/train", "val: images/val", "names:"]
    lines += [f"  {i}: {n}" for i, n in enumerate(names)]
    ypath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"data.yaml 생성: {ypath}")
    if not (Path(ds) / "images" / "val").is_dir():
        print("[알림] images/val 이 없습니다. 검증용 후보는 export --split val 로 따로 내보내세요.")


def export(a):
    root, ds = Path(a.reviewed), Path(a.dataset)
    img_dir, lbl_dir = ds / "images" / a.split, ds / "labels" / a.split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    n = {"good": 0, "other_litter": 0, "noise": 0}
    print(f"찾는 위치: {root.resolve()}")
    for kind in ("good", "other_litter", "noise"):
        kdir = root / kind
        if not kdir.is_dir():
            if kind != "other_litter":      # other_litter 는 선택 폴더
                print(f"[경고] 폴더 없음: {kdir.resolve()}")
            continue
        folders = sorted(p for p in kdir.glob("ID*") if p.is_dir())
        if not folders:
            print(f"[경고] {kdir.resolve()} 안에 ID* 폴더가 없습니다. 내용: {[p.name for p in kdir.iterdir()]}")
        for d in folders:
            if not (d / "frame.jpg").exists():
                print(f"[경고] {d.name}: frame.jpg 없음 (폴더 전체를 옮겼는지 확인). 내용: {[p.name for p in d.iterdir()]}")
                continue
            name = f"{a.prefix}_{d.name}"
            shutil.copy(d / "frame.jpg", img_dir / f"{name}.jpg")
            # noise -> 빈 라벨(배경 이미지, Hard Negative)
            if kind == "noise":
                text = ""
            else:
                text = scale_label((d / "frame.txt").read_text(), a.bbox_scale)
                # good = 꽁초(클래스 0), other_litter = 쓰레기(--litter-class). 모델이 예측한 클래스와 상관없이 검수 라벨이 우선
                text = set_class(text, a.litter_class if kind == "other_litter" else 0)
            (lbl_dir / f"{name}.txt").write_text(text)
            n[kind] += 1
    print(f"내보내기 완료: 꽁초 {n['good']}장, 쓰레기 {n['other_litter']}장, Hard Negative {n['noise']}장 -> {ds}")
    if n["other_litter"] or a.write_yaml:
        names = [x.strip() for x in a.names.split(",")]
        write_data_yaml(ds, names, None, a.yaml_path)


# ----------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine")
    m.add_argument("--video", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--debug-video", default=None)
    m.add_argument("--proc-fps", type=float, default=5, help="분석 fps")
    m.add_argument("--bg-len", type=int, default=30, help="배경 샘플 개수")
    m.add_argument("--bg-interval", type=float, default=2.0, help="배경 샘플 간격(초)")
    m.add_argument("--bg-min", type=int, default=10, help="배경 계산 시작 최소 샘플 수")
    m.add_argument("--bg-refresh", type=float, default=10.0, help="배경 재계산 주기(초)")
    m.add_argument("--diff-thr", type=int, default=30, help="배경 차이 임계값")
    m.add_argument("--motion-thr", type=int, default=15, help="움직임 임계값")
    m.add_argument("--motion-ratio", type=float, default=0.3, help="박스 내 움직임 비율 -> 동적 객체")
    m.add_argument("--occ-ratio", type=float, default=0.2, help="박스 주변 움직임 비율 -> 가려짐")
    m.add_argument("--min-area", type=int, default=15)
    m.add_argument("--max-area", type=int, default=800)
    m.add_argument("--persist-sec", type=float, default=15.0, help="최종 후보 판정 정지 시간")
    m.add_argument("--occ-grace", type=float, default=10.0, help="가려짐 유예 시간")
    m.add_argument("--miss-grace", type=float, default=2.0, help="사라짐 유예 시간")
    m.add_argument("--exclude-person", action="store_true", help="사람 검출(YOLO) 영역을 후보에서 제외")
    m.add_argument("--person-model", default="yolov8n.pt", help="사람 검출용 모델(COCO 사전학습)")
    m.add_argument("--person-conf", type=float, default=0.3)
    m.add_argument("--person-imgsz", type=int, default=960)
    m.add_argument("--person-margin", type=float, default=0.1, help="사람 박스 여백 비율")
    m.add_argument("--person-every", type=int, default=2, help="N번째 분석 프레임마다 사람 검출")
    m.add_argument("--neighbor-ratio", type=float, default=0.25,
                   help="후보 주변 차이 밀도가 이 값보다 크면 제외 (0이면 끔)")
    m.add_argument("--ignore-region", action="append", metavar="x,y,w,h",
                   help="분석 제외 영역 (여러 번 지정 가능)")
    m.add_argument("--dedupe-px", type=float, default=0, help="N>0 이면 이 거리(px) 안에 이미 저장한 후보가 있을 때 다시 저장하지 않음 (중복 감소)")
    m.add_argument("--dedupe-sec", type=float, default=1e9, help="--dedupe-px 중복 판정을 적용할 시간 범위(초, 기본 영상 전체)")
    m.add_argument("--stabilize", action="store_true", help="카메라의 작은 흔들림을 첫 프레임 기준으로 보정 (바람 등)")
    m.add_argument("--stabilize-max-shift", type=float, default=40, help="이보다 크게 이동하면 보정하지 않음(px)")
    m.add_argument("--config", default=None, help="카메라별 설정 JSON (옵션 이름의 '-'를 '_'로 쓴 키). 명령줄 옵션이 우선")
    m.set_defaults(fn=mine)

    e = sub.add_parser("export")
    e.add_argument("--reviewed", required=True, help="good/, noise/ 폴더가 있는 경로")
    e.add_argument("--dataset", required=True)
    e.add_argument("--prefix", required=True, help="파일명 충돌 방지용 (예: 영상 이름)")
    e.add_argument("--split", default="train")
    e.add_argument("--litter-class", type=int, default=1, help="other_litter(꽁초 외 쓰레기) 폴더에 부여할 클래스 번호")
    e.add_argument("--names", default="cigarette_butt,litter", help="data.yaml 클래스 이름(쉼표 구분, 순서=클래스 번호)")
    e.add_argument("--yaml-path", default=None, help="data.yaml 의 path 값(코랩에서 쓸 경로, 예: /content/dataset). 기본은 이 컴퓨터의 절대 경로")
    e.add_argument("--write-yaml", action="store_true", help="쓰레기 후보가 없어도 data.yaml 생성")
    e.add_argument("--bbox-scale", type=float, default=1.0,
                   help="라벨 박스를 중심 기준으로 N배 확대 (기본 1.0=그대로). 꽁초보다 박스가 작게 나올 때만 사용하고 결과를 확인할 것")
    e.set_defaults(fn=export)

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args()
    if known.config:
        cfg = json.loads(Path(known.config).read_text(encoding="utf-8"))
        valid = {act.dest for act in m._actions} - {"video", "out", "config", "help"}
        bad = [k for k in cfg if k.replace("-", "_") not in valid]
        if bad:
            raise SystemExit(f"설정 파일에 알 수 없는 키가 있습니다: {bad}\n사용 가능한 키: {sorted(valid)}")
        m.set_defaults(**{k.replace("-", "_"): v for k, v in cfg.items()})

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
