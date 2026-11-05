r"""
make_scene.py - 후보가 처음 보인 시점 전후의 '장면 사진'(scene.jpg)과 선택적으로 짧은 영상(scene.mp4)을 만든다.

  python make_scene.py --candidates runs/batch_a50/<영상키>/candidates --dir <영상 폴더 또는 zip> --ids ID1237
  python make_scene.py --candidates runs/batch_a50/<영상키>/candidates --dir <영상 폴더 또는 zip> --min-area 5000 --max 10
  python make_scene.py ... --ids ID1237 --mosaic            # 사람(YOLO person) 영역을 모자이크
  python make_scene.py ... --ids ID1237 --clip --mosaic      # scene.mp4 도 저장(내려받기용)
  python make_scene.py ... --ids ID1237 --gif --mosaic       # scene.gif(움직이는 사진, 대시보드에서 재생) 도 저장. pip install pillow 필요

- --dir 은 run_pipeline/batch_run 때 쓴 영상 위치(폴더나 zip)와 같게 주세요. 영상 키(폴더 이름 = 영상 파일 이름)로 영상을 찾습니다.
- scene.jpg 는 후보가 처음 보인 시각(first_seen_sec) 전후 장면을 바둑판으로 붙인 사진입니다. 빨간 상자가 후보 위치입니다.
  '무단투기 하는 순간'을 찾아낸 것이 아니라 '변화가 시작된 시각 전후'를 자동으로 고른 것입니다. 사람이 직접 확인하세요.
- --mosaic 은 YOLO(yolov8n, 사람 클래스)로 찾은 사람 영역을 모자이크합니다. 작거나 가려진 사람, 오검출은 놓칠 수 있어
  개인정보 보호를 보장하지 않습니다(외부 공개 전에는 사람이 확인). ultralytics 가 필요합니다(pip install ultralytics).
- scene.gif 는 짧은 구간(기본 처음 보인 시각 -6초 ~ +4초)을 움직이는 사진으로 만든 것입니다. upload_candidates.py 가 scene.gif 가 있으면
  jpg 대신 올립니다(서버가 거절하면 scene.jpg 로 대신 올림). 너비 480px 로 줄여 용량을 작게 만듭니다(--gif-width).
- scene.mp4 는 OpenCV(mp4v)로 저장하므로 일부 브라우저에서는 재생이 안 될 수 있습니다(영상 플레이어로 여세요).
  서버에는 영상 저장 기능이 없어서 대시보드에는 올라가지 않고 scene.jpg 만 올라갑니다(upload_candidates.py).
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from video_source import list_videos, open_video  # noqa: E402


def find_item(root, key):
    for it in list_videos(root):
        if Path(it["name"].split("::")[-1]).stem == key:
            return it
    return None


def pixelate(img, box, block=16):
    x, y, w, h = [int(v) for v in box]
    H, W = img.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, W - x), min(h, H - y)
    if w <= 0 or h <= 0:
        return
    roi = img[y:y + h, x:x + w]
    small = cv2.resize(roi, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR)
    img[y:y + h, x:x + w] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


class Mosaic:
    """butt_miner 의 사람 검출(load_person_detector/detect_persons)을 그대로 쓴다."""

    def __init__(self, model="yolov8n.pt", conf=0.25, imgsz=960, margin=0.15):
        import butt_miner
        self.a = SimpleNamespace(person_model=model, person_conf=conf, person_imgsz=imgsz, person_margin=margin)
        self.model = butt_miner.load_person_detector(self.a)
        self.detect = butt_miner.detect_persons

    def apply(self, frame):
        n = 0
        for b in self.detect(self.model, frame, self.a):
            pixelate(frame, b)
            n += 1
        return n


def read_at(cap, fps, t):
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
    ok, fr = cap.read()
    return fr if ok else None


def draw_box(img, box, scale=1.0, color=(0, 0, 255)):
    x, y, w, h = [int(v * scale) for v in box]
    cv2.rectangle(img, (x - 3, y - 3), (x + w + 3, y + h + 3), color, max(2, int(3 * scale * 2)))


def make_strip(cap, fps, length, meta, pre, post, n, mosaic, cols=3, tile_w=640):
    fs = float(meta["first_seen_sec"])
    t0, t1 = max(0.0, fs - pre), min(length - 0.1, fs + post)
    times = list(np.linspace(t0, max(t0, t1), n))
    tiles, hidden = [], 0
    for t in times:
        fr = read_at(cap, fps, t)
        if fr is None:
            continue
        if mosaic is not None:
            hidden += mosaic.apply(fr)
        sc = tile_w / fr.shape[1]
        fr = cv2.resize(fr, (tile_w, int(fr.shape[0] * sc)))
        draw_box(fr, meta["bbox_xywh"], sc)
        rel = t - fs
        cv2.putText(fr, f"t={t:.1f}s ({rel:+.1f}s)", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(fr, f"t={t:.1f}s ({rel:+.1f}s)", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
        tiles.append(fr)
    if not tiles:
        return None, 0
    th = tiles[0].shape[0]
    while len(tiles) % cols:
        tiles.append(np.zeros((th, tile_w, 3), np.uint8))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    body = np.vstack(rows)
    title = np.zeros((44, body.shape[1], 3), np.uint8)
    cv2.putText(title, f"ID {meta['id']}  first change at {fs:.1f}s (red box = candidate)"
                       f"{'  [persons mosaiced]' if mosaic is not None else ''}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
    return np.vstack([title, body]), hidden


def make_clip(cap, fps, length, meta, pre, post, mosaic, path, max_w=960):
    fs = float(meta["first_seen_sec"])
    t0, t1 = max(0.0, fs - pre), min(length - 0.1, fs + post)
    i0, i1 = int(round(t0 * fps)), int(round(t1 * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, i0)
    out = None
    for i in range(i0, i1 + 1):
        ok, fr = cap.read()
        if not ok:
            break
        if mosaic is not None:
            mosaic.apply(fr)
        sc = min(1.0, max_w / fr.shape[1])
        if sc < 1.0:
            fr = cv2.resize(fr, (int(fr.shape[1] * sc), int(fr.shape[0] * sc)))
        if i / fps >= fs:
            draw_box(fr, meta["bbox_xywh"], sc)
        cv2.putText(fr, f"t={i / fps:.1f}s", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        if out is None:
            out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (fr.shape[1], fr.shape[0]))
        out.write(fr)
    if out is not None:
        out.release()
        return True
    return False


def make_gif(cap, fps, length, meta, pre, post, mosaic, path, width=480, max_frames=40):
    from PIL import Image
    fs = float(meta["first_seen_sec"])
    t0, t1 = max(0.0, fs - pre), min(length - 0.1, fs + post)
    i0, i1 = int(round(t0 * fps)), int(round(t1 * fps))
    step = max(1, int(np.ceil((i1 - i0 + 1) / max_frames)))
    frames = []
    for i in range(i0, i1 + 1, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            break
        if mosaic is not None:
            mosaic.apply(fr)
        sc = width / fr.shape[1]
        fr = cv2.resize(fr, (width, int(fr.shape[0] * sc)))
        if i / fps >= fs:
            draw_box(fr, meta["bbox_xywh"], sc)
        cv2.putText(fr, f"t={i / fps:.1f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        frames.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
    if not frames:
        return False
    dur = int(round(1000.0 * step / fps))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=dur, loop=0, optimize=True)
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True, help="ID**** 폴더들이 있는 경로(…/<영상키>/candidates)")
    p.add_argument("--dir", required=True, help="원본 영상이 있는 폴더 또는 zip(batch_run 때의 --dir 과 같게)")
    p.add_argument("--ids", default="", help="쉼표로 구분한 후보 폴더 이름(예: ID1237,ID2000)")
    p.add_argument("--min-area", type=int, default=0, help="면적(px²)이 이 값 이상인 후보 전부")
    p.add_argument("--max", type=int, default=20, help="--min-area 로 고를 때 최대 개수(큰 것부터)")
    p.add_argument("--pre", type=float, default=6.0, help="처음 보인 시각 이전 초")
    p.add_argument("--post", type=float, default=4.0, help="처음 보인 시각 이후 초")
    p.add_argument("--frames", type=int, default=6, help="장면 사진에 넣을 프레임 수(3의 배수 권장)")
    p.add_argument("--mosaic", action="store_true", help="사람 영역 모자이크(ultralytics 필요)")
    p.add_argument("--person-model", default="yolov8n.pt")
    p.add_argument("--clip", action="store_true", help="scene.mp4 도 저장")
    p.add_argument("--gif", action="store_true", help="scene.gif(움직이는 사진)도 저장(pillow 필요)")
    p.add_argument("--gif-width", type=int, default=480)
    p.add_argument("--key", default="", help="영상 키(기본: candidates 폴더의 상위 폴더 이름)")
    p.add_argument("--force", action="store_true", help="이미 있는 scene.jpg 도 다시 만든다")
    a = p.parse_args()

    root = Path(a.candidates)
    if not root.is_dir():
        sys.exit(f"후보 폴더가 없습니다: {root.resolve()}")
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    if not ids and a.min_area <= 0:
        sys.exit("--ids 또는 --min-area 중 하나는 주세요(전체 후보에 만들면 너무 오래 걸립니다).")
    metas = {}
    for d in sorted(root.glob("ID*")):
        mp = d / "meta.json"
        if mp.is_file():
            metas[d.name] = json.loads(mp.read_text(encoding="utf-8"))
    if ids:
        missing = [i for i in ids if i not in metas]
        if missing:
            sys.exit(f"후보 폴더를 찾을 수 없습니다: {missing}")
        targets = ids
    else:
        big = [(m.get("area_px") or m["bbox_xywh"][2] * m["bbox_xywh"][3], n) for n, m in metas.items()]
        targets = [n for ar, n in sorted(big, reverse=True) if ar >= a.min_area][: a.max]
    if not targets:
        sys.exit("조건에 맞는 후보가 없습니다.")
    todo = [n for n in targets if a.force or not (root / n / "scene.jpg").exists() or (a.gif and not (root / n / "scene.gif").exists())]
    print(f"대상 {len(targets)}개 중 새로 만들 것 {len(todo)}개: {todo}")
    if not todo:
        return

    key = a.key or root.resolve().parent.name
    item = find_item(a.dir, key)
    if item is None:
        sys.exit(f"영상 키 '{key}' 에 맞는 영상을 --dir 에서 찾지 못했습니다: {a.dir}\n  --key 로 직접 지정하거나 --dir 를 확인하세요.")
    mosaic = Mosaic(a.person_model) if a.mosaic else None

    with open_video(item) as vp:
        cap = cv2.VideoCapture(vp)
        if not cap.isOpened():
            sys.exit(f"영상을 열 수 없습니다: {vp}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        length = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        for n in todo:
            m = metas[n]
            img, hidden = make_strip(cap, fps, length, m, a.pre, a.post, a.frames, mosaic)
            if img is None:
                print(f"[건너뜀] {n}: 프레임을 읽지 못했습니다")
                continue
            cv2.imwrite(str(root / n / "scene.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            msg = f"[저장] {n}/scene.jpg"
            if mosaic is not None:
                msg += f"  (모자이크한 사람 영역 {hidden}곳)"
            if a.gif:
                try:
                    ok = make_gif(cap, fps, length, m, a.pre, a.post, mosaic, root / n / "scene.gif", a.gif_width)
                    msg += "  + scene.gif" if ok else "  (gif 실패)"
                except ImportError:
                    msg += "  (gif 실패: pip install pillow)"
            if a.clip:
                ok = make_clip(cap, fps, length, m, a.pre, a.post, mosaic, root / n / "scene.mp4")
                msg += "  + scene.mp4" if ok else "  (mp4 실패)"
            print(msg)
        cap.release()
    print("\n끝. 대시보드에 올리려면 upload_candidates.py 를 --ids 와 --redo 로 실행하세요.")


if __name__ == "__main__":
    main()
