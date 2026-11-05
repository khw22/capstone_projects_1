"""
auto_onboard.py - 카메라 자동 온보딩: 영상 앞부분(쓰레기가 없다고 가정한 구간)에서 '계속 흔들리는 구역'(수풀, 나뭇잎, 수면 등)을 스스로 찾아
분석에서 제외할 영역 목록(ignore_region)을 만든다. 사람이 카메라마다 손으로 제외 영역을 정하던 일을 대신한다.

  python auto_onboard.py --video 영상.mp4 --out runs/onb --sec 30

방법: 앞부분 N초의 프레임을 모아 픽셀마다 '시간에 따른 밝기 변화의 중앙값 절대편차(MAD)'를 구한다.
  - 수풀처럼 계속 흔들리는 곳은 MAD 가 크고, 잠깐 지나가는 사람은 대부분의 프레임에서 그 자리에 없으므로 MAD 가 작다(중앙값 기반이라 이상치에 강함).
  - 기준은 이 카메라의 잡음 수준(MAD 중앙값)의 3배, 최소 6.  제외 영역이 화면의 35%를 넘지 않도록 기준을 자동으로 올린다.
  - 큰 단위(타일)로 묶어 직사각형 목록으로 바꾼다(butt_miner --ignore-region 형식).
전제와 한계: 앞부분에 쓰레기가 이미 놓여 있거나 오래 머물면 그 자리도 제외될 수 있다(결과 이미지 onboarding.jpg 로 확인).
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def _runs(row):
    out, start = [], None
    for i, v in enumerate(row):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(row) - 1))
    return out


def tiles_to_rects(flag):
    """타일 격자(bool)를 직사각형 목록으로 합친다. 반환: [(col0, row0, col1, row1)]"""
    rects, active = [], {}
    for r in range(flag.shape[0] + 1):
        runs = set(_runs(flag[r])) if r < flag.shape[0] else set()
        for key in list(active):
            if key not in runs:
                rects.append((key[0], active.pop(key), key[1], r - 1))
        for key in runs:
            if key not in active:
                active[key] = r
    return rects


def learn(video, sec=30.0, max_frames=120, tile=40, min_frac=0.3, floor=6.0, cap=0.35, pad=1):
    cp = cv2.VideoCapture(str(video))
    if not cp.isOpened():
        raise SystemExit(f"영상을 열 수 없습니다: {video}")
    fps = cp.get(cv2.CAP_PROP_FPS) or 30
    W, H = int(cp.get(3)), int(cp.get(4))
    sc = 0.5 if W > 960 else 1.0
    w, h = int(W * sc), int(H * sc)
    n_total = int(sec * fps)
    step = max(1, n_total // max_frames)
    frames, ref, win, first, idx = [], None, None, None, 0
    while idx < n_total:
        ok, f = cp.read()
        if not ok:
            break
        if first is None:
            first = f.copy()
        if idx % step == 0:
            g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)
            if ref is None:
                ref, win = g, cv2.createHanningWindow((w, h), cv2.CV_32F)
            else:                                           # 카메라가 조금 흔들려도 정렬해서 MAD 가 커지지 않게 함
                (dx, dy), _ = cv2.phaseCorrelate(ref, g, win)
                if abs(dx) + abs(dy) <= 20:
                    g = cv2.warpAffine(g, np.float32([[1, 0, -dx], [0, 1, -dy]]), (w, h), borderMode=cv2.BORDER_REFLECT)
            frames.append(g)
        idx += 1
    cp.release()
    if len(frames) < 8:
        return None
    arr = np.stack(frames)
    med = np.median(arr, axis=0)
    mad = cv2.blur(np.median(np.abs(arr - med), axis=0), (7, 7))
    base = float(np.median(mad))
    thr = max(floor, 3.0 * base)
    ts = max(8, int(tile * sc))
    gh, gw = h // ts, w // ts
    for _ in range(12):
        mask = (mad >= thr).astype(np.float32)
        flag = np.zeros((gh, gw), bool)
        for r in range(gh):
            for c in range(gw):
                flag[r, c] = mask[r * ts:(r + 1) * ts, c * ts:(c + 1) * ts].mean() >= min_frac
        if flag.any() and pad > 0:                          # 가장자리 한 칸을 더 덮는다(안 그러면 제외 영역 경계에 남은 조각이 후보가 됨)
            grown = flag.copy()
            for dr in range(-pad, pad + 1):
                for dc in range(-pad, pad + 1):
                    grown |= np.roll(np.roll(flag, dr, axis=0), dc, axis=1)
            flag = grown
        rects = tiles_to_rects(flag) if flag.any() else []
        area = sum((c1 - c0 + 1) * (r1 - r0 + 1) for c0, r0, c1, r1 in rects) * ts * ts
        if area / (w * h) <= cap:
            break
        thr *= 1.25                                         # 제외 영역이 너무 넓으면 기준을 올려 줄임
    regions = []
    for c0, r0, c1, r1 in rects:
        x, y = int(c0 * ts / sc), int(r0 * ts / sc)
        regions.append((x, y, min(W - x, int((c1 - c0 + 1) * ts / sc)), min(H - y, int((r1 - r0 + 1) * ts / sc))))
    frac = sum(rw * rh for _, _, rw, rh in regions) / (W * H)
    return {"ignore_region": [",".join(map(str, r)) for r in regions], "ignored_fraction": round(frac, 4), "sec_used": round(len(frames) * step / fps, 1),
            "frames": len(frames), "noise_floor": round(base, 2), "threshold": round(thr, 2), "size": [W, H], "first_frame": first}


def draw(res):
    img = res["first_frame"].copy()
    over = img.copy()
    for r in res["ignore_region"]:
        x, y, w, h = map(int, r.split(","))
        cv2.rectangle(over, (x, y), (x + w, y + h), (0, 0, 255), -1)
    img = cv2.addWeighted(over, 0.4, img, 0.6, 0)
    for r in res["ignore_region"]:
        x, y, w, h = map(int, r.split(","))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 2)
    s = max(0.6, img.shape[1] / 1920)
    cv2.putText(img, f"auto-onboarding: {len(res['ignore_region'])} zones, {res['ignored_fraction'] * 100:.1f}% of frame ignored",
                (12, int(36 * s)), cv2.FONT_HERSHEY_SIMPLEX, 1.0 * s, (0, 255, 255), max(2, int(2 * s)), cv2.LINE_AA)
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", default="runs/onboarding")
    p.add_argument("--sec", type=float, default=30.0, help="쓰레기가 없다고 가정할 영상 앞부분 길이(초)")
    a = p.parse_args()
    res = learn(a.video, a.sec)
    if res is None:
        raise SystemExit("앞부분에서 읽은 프레임이 너무 적습니다(영상이 짧거나 --sec 가 너무 작음).")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "onboarding.jpg"), draw(res), [cv2.IMWRITE_JPEG_QUALITY, 90])
    info = {k: v for k, v in res.items() if k != "first_frame"}
    (out / "onboarding.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"앞 {info['sec_used']}초({info['frames']}프레임)로 학습: 잡음 수준 {info['noise_floor']}, 기준 {info['threshold']}")
    print(f"제외 영역 {len(info['ignore_region'])}개, 화면의 {info['ignored_fraction'] * 100:.1f}%")
    print(f"이미지: {out / 'onboarding.jpg'} (빨간 부분이 제외 영역, 봉투가 놓일 자리가 빨갛게 덮이지 않았는지 확인)")


if __name__ == "__main__":
    main()
