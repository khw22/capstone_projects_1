"""
measure_shake.py - 영상에서 카메라 흔들림 크기(px)를 측정

  python measure_shake.py --video 영상.mp4 [--every 5] [--max-frames 600]

첫 프레임을 기준으로 이후 프레임이 평행 이동한 양을 위상 상관으로 잰다(원본 해상도 px 환산).
사람이나 물체가 크게 움직이는 장면에서는 값이 부정확할 수 있다.
"""
import argparse

import cv2
import numpy as np


def measure(video, every=5, max_frames=900):
    """첫 프레임 대비 이동량을 잰다. dict(width, height, fps, total, mags, pts) 반환."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"영상을 열 수 없습니다: {video}")
    ok, f = cap.read()
    if not ok:
        raise SystemExit("첫 프레임을 읽을 수 없습니다.")
    scale = min(1.0, 640.0 / f.shape[1])
    info = {"width": f.shape[1], "height": f.shape[0], "fps": cap.get(cv2.CAP_PROP_FPS) or 0,
            "total": int(cap.get(cv2.CAP_PROP_FRAME_COUNT))}

    def prep(x):
        g = cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
        return np.float32(cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))

    ref = prep(f)
    win = cv2.createHanningWindow((ref.shape[1], ref.shape[0]), cv2.CV_32F)
    mags, pts, i = [], [], 0
    while i < max_frames:
        ok, f = cap.read()
        if not ok:
            break
        i += 1
        if i % every:
            continue
        (dx, dy), _ = cv2.phaseCorrelate(ref, prep(f), win)
        mags.append(float(np.hypot(dx, dy)) / scale)
        pts.append((dx / scale, dy / scale))
    cap.release()
    info["mags"], info["pts"] = mags, pts
    return info


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--every", type=int, default=5, help="N프레임마다 측정")
    p.add_argument("--max-frames", type=int, default=900)
    a = p.parse_args()
    r = measure(a.video, a.every, a.max_frames)
    fps, total = r["fps"], r["total"]
    print(f"영상: {r['width']}x{r['height']}, {fps:.1f}fps, {total}프레임" + (f" (약 {total / fps:.0f}초)" if fps else ""))
    if not r["mags"]:
        raise SystemExit("측정할 프레임이 없습니다.")
    m = np.array(r["mags"])
    P = np.array(r["pts"])
    print(f"측정 {len(m)}회 | 기준 프레임 대비 이동량(px): 평균 {m.mean():.2f}, 95% {np.percentile(m, 95):.2f}, 최대 {m.max():.2f}")
    jit = np.hypot(*np.diff(P, axis=0).T) if len(P) > 1 else np.array([0.0])
    k = max(1, len(m) // 3)
    print(f"시간 경과: 앞 1/3 평균 {m[:k].mean():.2f}px -> 뒤 1/3 평균 {m[-k:].mean():.2f}px | "
          f"측정 간 떨림(연속 측정 사이 변화) 평균 {jit.mean():.2f}px, 최대 {jit.max():.2f}px")
    if m[-k:].mean() - m[:k].mean() > 2 and jit.mean() < 0.5 * m.mean():
        print("해석: 시간이 갈수록 한쪽으로 천천히 밀리는 '드리프트' 형태입니다. 첫 프레임 기준 보정이 비교적 잘 맞을 것으로 예상됩니다(드리프트 영상으로 보정 시험은 하지 못했습니다).")
    elif jit.mean() >= 0.5 * m.mean():
        print("해석: 프레임 사이에서 크게 떨리는 형태입니다. 후보 추출에 더 불리하고 보정이 덜 정확할 수 있습니다.")
    if np.percentile(m, 95) < 0.7:
        print("판단(시험 기반 추정): 흔들림이 매우 작아 보정 없이 써도 될 가능성이 큽니다.")
    elif m.max() <= 40:
        print("판단(시험 기반 추정): butt_miner mine 에 --stabilize 를 쓰는 것을 권장합니다.")
    else:
        print("이동량이 커서 평행 이동 보정으로 부족할 수 있습니다. 카메라 고정이나 촬영 조건 개선을 먼저 하세요.")


if __name__ == "__main__":
    main()
