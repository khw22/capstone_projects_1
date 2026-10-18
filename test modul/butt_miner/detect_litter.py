"""
detect_litter.py - 학습된 YOLO(꽁초/쓰레기)로 영상·스트림에서 자동 감지하고 서버에 알린다

흐름: 프레임 -> YOLO 검출(필요하면 타일 추론) -> 사람 영역 제외(선택) -> 위치 기반 추적
      -> '같은 자리에서 N초 이상 정지 + 검출 비율 충족'이면 이벤트 1회 발생 -> 저장·업로드

  python detect_litter.py --model best.pt --source 영상.mp4 --out runs/detect1 --imgsz 1280 --persist-sec 10
  python detect_litter.py --model best.pt --source rtsp://주소 --out runs/live --server http://localhost:8000 --device-id local-ai-server

- 이벤트 폴더(out/candidates/ID****)는 butt_miner 후보와 같은 형식이라 upload/pull_reviews/export 흐름(검수 -> 재학습)에 그대로 쓸 수 있다.
- 서버에는 event_type = <클래스명>_detected (예: cigarette_butt_detected, litter_detected)로 보낸다.
- 움직이는 물체는 정지 시간이 계속 초기화되므로 이벤트가 되지 않는다.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from math import hypot
from pathlib import Path

import cv2
import numpy as np

KO = {"cigarette_butt": "꽁초", "litter": "쓰레기"}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def size(b):
    return (b[2] - b[0], b[3] - b[1])


# ------------------------------------------------------------ 검출기
def nms(dets, iou_thr=0.5):
    """dets: [x1,y1,x2,y2,conf,cls] 목록. 클래스별 NMS."""
    keep = []
    for cls in {d[5] for d in dets}:
        items = sorted((d for d in dets if d[5] == cls), key=lambda d: -d[4])
        while items:
            best = items.pop(0)
            keep.append(best)
            rest = []
            for d in items:
                x1, y1 = max(best[0], d[0]), max(best[1], d[1])
                x2, y2 = min(best[2], d[2]), min(best[3], d[3])
                inter = max(0, x2 - x1) * max(0, y2 - y1)
                union = (best[2] - best[0]) * (best[3] - best[1]) + (d[2] - d[0]) * (d[3] - d[1]) - inter
                if union <= 0 or inter / union < iou_thr:
                    rest.append(d)
            items = rest
    return keep


class YoloDetector:
    def __init__(self, path, imgsz, conf, tile, overlap):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise SystemExit("ultralytics 가 필요합니다: pip install ultralytics")
        self.model = YOLO(path)
        self.names = self.model.names
        self.imgsz, self.conf, self.tile, self.overlap = imgsz, conf, tile, overlap

    def detect(self, frame):
        H, W = frame.shape[:2]
        if self.tile > 0 and (W > self.tile or H > self.tile):
            t = min(self.tile, W, H)
            step = max(1, int(t * (1 - self.overlap)))
            xs = list(range(0, W - t + 1, step))
            ys = list(range(0, H - t + 1, step))
            if xs[-1] + t < W:
                xs.append(W - t)
            if ys[-1] + t < H:
                ys.append(H - t)
            tiles = [(x, y, frame[y:y + t, x:x + t]) for y in ys for x in xs]
        else:
            tiles = [(0, 0, frame)]
        results = self.model.predict([t[2] for t in tiles], imgsz=self.imgsz, conf=self.conf, verbose=False)
        out = []
        for (ox, oy, _), r in zip(tiles, results):
            for (x1, y1, x2, y2), c, k in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(),
                                              r.boxes.cls.cpu().numpy()):
                out.append([x1 + ox, y1 + oy, x2 + ox, y2 + oy, float(c), int(k)])
        return nms(out) if len(tiles) > 1 else out


class PersonFilter:
    def __init__(self, path, conf, margin, every):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise SystemExit("ultralytics 가 필요합니다: pip install ultralytics")
        self.m, self.conf, self.margin, self.every, self.n, self.boxes = YOLO(path), conf, margin, every, 0, []

    def update(self, frame):
        if self.n % self.every == 0:
            r = self.m.predict(frame, classes=[0], conf=self.conf, imgsz=960, verbose=False)[0]
            self.boxes = []
            for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy():
                m = max(x2 - x1, y2 - y1) * self.margin + 4
                self.boxes.append((x1 - m, y1 - m, x2 + m, y2 + m))
        self.n += 1

    def inside(self, d):
        cx, cy = center(d)
        return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in self.boxes)


# ------------------------------------------------------------ 추적
class Track:
    def __init__(self, tid, d, t):
        self.id, self.box, self.last_t, self.emitted = tid, d[:4], t, False
        self._reset(d, t)

    def _reset(self, d, t):
        self.anchor, self.anchor_t, self.seen, self.frames = center(d[:4]), t, 1, 1
        self.csum, self.cn = {d[5]: d[4]}, {d[5]: 1}

    def tol(self):
        w, h = size(self.box)
        return max(12.0, 0.6 * max(w, h))

    def hit(self, d, t):
        self.box, self.last_t = d[:4], t
        c = center(d[:4])
        if hypot(c[0] - self.anchor[0], c[1] - self.anchor[1]) > self.tol():
            self._reset(d, t)               # 움직이는 물체: 정지 시간을 다시 센다
            return
        self.seen += 1
        self.csum[d[5]] = self.csum.get(d[5], 0) + d[4]
        self.cn[d[5]] = self.cn.get(d[5], 0) + 1

    def cls(self):
        return max(self.csum, key=self.csum.get)       # 가장 높은 신뢰도 합을 얻은 클래스

    def conf_mean(self):
        k = self.cls()
        return self.csum[k] / self.cn[k]


class Tracker:
    def __init__(self, a):
        self.a, self.tracks, self.next_id, self.emitted = a, [], 1, []

    def update(self, dets, t):
        for tr in self.tracks:
            tr.frames += 1
        free = list(self.tracks)
        for d in sorted(dets, key=lambda x: -x[4]):
            cx, cy = center(d)
            w, h = size(d)
            best, bd = None, 1e9
            for tr in free:
                tcx, tcy = center(tr.box)
                dist = hypot(cx - tcx, cy - tcy)
                if dist < max(20.0, max(w, h, *size(tr.box))) and dist < bd:
                    best, bd = tr, dist
            if best:
                free.remove(best)
                best.hit(d, t)
            else:
                self.tracks.append(Track(self.next_id, d, t))
                self.next_id += 1
        self.tracks = [tr for tr in self.tracks if t - tr.last_t <= self.a.miss_sec]

    def ready(self, t):
        a, out = self.a, []
        for tr in self.tracks:
            if tr.emitted:
                continue
            if t - tr.anchor_t >= a.persist_sec and tr.seen / tr.frames >= a.min_ratio and tr.conf_mean() >= a.event_conf:
                tr.emitted = True
                c = center(tr.box)
                if any(hypot(c[0] - e[0], c[1] - e[1]) < tr.tol() and t - e[2] < a.cooldown_sec for e in self.emitted):
                    continue                # 같은 자리에서 이미 알린 물체
                self.emitted.append((c[0], c[1], t))
                out.append(tr)
        return out


# ------------------------------------------------------------ 서버
class Server:
    def __init__(self, base, device, key, timeout):
        import requests
        self.r, self.base, self.device, self.timeout = requests, base.rstrip("/"), device, timeout
        self.h = {"X-Device-Api-Key": key} if key else {}

    def check(self):
        r = self.r.get(f"{self.base}/api/devices", timeout=self.timeout)
        ids = [d["device_id"] for d in r.json().get("devices", [])] if r.ok else None
        if ids is not None and self.device not in ids:
            raise SystemExit(f"서버에 등록되지 않은 장치입니다: {self.device}\n  등록된 장치: {ids}")

    def snapshot(self, path, ts, frame_id):
        with open(path, "rb") as f:
            r = self.r.post(f"{self.base}/api/devices/{self.device}/snapshots", headers=self.h, timeout=self.timeout,
                            files={"file": ("panel.jpg", f, "image/jpeg")},
                            data={"metadata": json.dumps({"timestamp": ts, "frame_id": frame_id})})
        r.raise_for_status()
        return r.json()["snapshot_id"]

    def post(self, kind, payload):
        r = self.r.post(f"{self.base}/api/devices/{self.device}/{kind}", headers=self.h, json=payload, timeout=self.timeout)
        r.raise_for_status()


def fit(img, w, h):
    s = min(w / img.shape[1], h / img.shape[0])
    r = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=cv2.INTER_NEAREST)
    c = np.zeros((h, w, 3), np.uint8)
    c[:r.shape[0], :r.shape[1]] = r
    return c


def make_panel(frame, box, title):
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    vis = frame.copy()
    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), max(2, W // 640))
    left = cv2.resize(vis, (640, max(1, int(640 * H / W))))
    m = int(max(x2 - x1, y2 - y1) * 2) + 20
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    zoom = fit(frame[max(0, cy - m):min(H, cy + m), max(0, cx - m):min(W, cx + m)], left.shape[0], left.shape[0])
    panel = np.hstack([left, zoom])
    cv2.putText(panel, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return panel


# ------------------------------------------------------------ 메인
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--source", required=True, help="영상 파일, RTSP/HTTP 주소, 또는 카메라 번호")
    p.add_argument("--out", required=True)
    p.add_argument("--imgsz", type=int, default=1280)
    p.add_argument("--conf", type=float, default=0.25, help="YOLO 검출 신뢰도 하한")
    p.add_argument("--tile", type=int, default=0, help="N>0 이면 NxN 타일로 나눠 추론 (작은 물체용, 느려짐)")
    p.add_argument("--overlap", type=float, default=0.2)
    p.add_argument("--proc-fps", type=float, default=5)
    p.add_argument("--persist-sec", type=float, default=10, help="같은 자리에서 이 시간 이상 정지하면 이벤트")
    p.add_argument("--min-ratio", type=float, default=0.6, help="정지 구간에서 검출된 프레임 비율 하한")
    p.add_argument("--event-conf", type=float, default=0.35, help="이벤트로 인정할 평균 신뢰도 하한")
    p.add_argument("--miss-sec", type=float, default=3, help="이 시간 동안 안 보이면 추적 종료")
    p.add_argument("--cooldown-sec", type=float, default=600, help="같은 자리 재알림 억제 시간")
    p.add_argument("--exclude-person", action="store_true")
    p.add_argument("--person-model", default="yolov8n.pt")
    p.add_argument("--person-margin", type=float, default=0.1)
    p.add_argument("--person-every", type=int, default=2)
    p.add_argument("--server", default="")
    p.add_argument("--device-id", default="local-ai-server")
    p.add_argument("--api-key", default=os.environ.get("DEVICE_API_KEY", ""))
    p.add_argument("--video-name", default="")
    p.add_argument("--video-start", default="", help="파일 입력일 때 영상 시작 실제 시각(UTC ISO). 없으면 현재 시각")
    p.add_argument("--heartbeat-sec", type=float, default=60, help="0이면 heartbeat 안 보냄")
    p.add_argument("--severity", default="info", choices=["info", "warning", "critical"])
    p.add_argument("--save-video", action="store_true")
    p.add_argument("--max-sec", type=float, default=0)
    p.add_argument("--gt", default="", help="정답 json(make_synthetic/make_gt). 진단용: 정답 위치에서 YOLO가 프레임마다 얼마나 검출했는지 출력")
    p.add_argument("--reconnect", type=int, default=5, help="스트림 끊김 시 재연결 시도 횟수")
    a = p.parse_args()

    detector = YoloDetector(a.model, a.imgsz, a.conf, a.tile, a.overlap)
    names = detector.names
    pf = PersonFilter(a.person_model, 0.3, a.person_margin, a.person_every) if a.exclude_person else None
    srv = None
    if a.server:
        srv = Server(a.server, a.device_id, a.api_key, 20)
        try:
            srv.check()
        except SystemExit:
            raise
        except Exception as e:
            raise SystemExit(f"서버에 연결할 수 없습니다 ({a.server}): {e}")

    src = a.source
    is_stream = src.isdigit() or src.lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        raise SystemExit(f"입력을 열 수 없습니다: {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps / a.proc_fps)))
    start = datetime.strptime(a.video_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if a.video_start else None
    prefix = Path(a.video_name).stem if a.video_name else (Path(src).stem if not is_stream else "stream")

    out = Path(a.out)
    (out / "candidates").mkdir(parents=True, exist_ok=True)
    log_path = out / "candidates" / "uploaded.json"
    uploaded = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}
    tracker = Tracker(a)
    gt_items, conf_all, n_dets = [], [], 0
    if a.gt:
        for g in json.loads(Path(a.gt).read_text(encoding="utf-8"))["butts"]:
            x, y, w_, h_ = g["bbox_xywh"]
            gt_items.append({"id": g["id"], "c": (x + w_ / 2, y + h_ / 2), "r": max(25.0, max(w_, h_)),
                             "t0": g["start_sec"], "t1": g["end_sec"], "frames": 0, "hits": 0, "maxc": 0.0, "box": g["bbox_xywh"]})
    writer, n_events, idx, n_proc, lat_sum = None, len(list((out / "candidates").glob("ID*"))), -1, 0, 0.0
    wall0, last_proc, last_hb = time.time(), 0.0, 0.0
    fails = 0
    print(f"시작: {src} (분석 {a.proc_fps}fps, 정지 {a.persist_sec}초, 클래스 {dict(names)})")

    try:
        while True:
            if is_stream:
                ok = cap.grab()
                if not ok:
                    fails += 1
                    if fails > a.reconnect:
                        print("스트림이 끊겨 종료합니다.")
                        break
                    print(f"[재연결] {fails}/{a.reconnect}")
                    cap.release()
                    time.sleep(2)
                    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
                    continue
                now = time.time()
                if now - last_proc < 1.0 / a.proc_fps:
                    continue
                last_proc = now
                ok, frame = cap.retrieve()
                t = now - wall0
            else:
                ok, frame = cap.read()
                if not ok:
                    break
                idx += 1
                if idx % step:
                    continue
                t = idx / fps
            if not ok:
                continue
            if a.max_sec and t > a.max_sec:
                break

            t_start = time.perf_counter()
            dets = detector.detect(frame)
            n_dets += len(dets)
            if len(conf_all) < 200000:
                conf_all.extend(d[4] for d in dets)
            for gi in gt_items:
                if gi["t0"] <= t <= gi["t1"]:
                    gi["frames"] += 1
                    near = [d for d in dets if hypot(center(d)[0] - gi["c"][0], center(d)[1] - gi["c"][1]) <= gi["r"]]
                    if near:
                        gi["hits"] += 1
                        gi["maxc"] = max(gi["maxc"], max(d[4] for d in near))
            if pf:
                pf.update(frame)
                dets = [d for d in dets if not pf.inside(d)]
            tracker.update(dets, t)
            lat_sum += time.perf_counter() - t_start
            n_proc += 1

            for tr in tracker.ready(t):
                n_events += 1
                raw_name = str(names[tr.cls()]) if tr.cls() in names else str(tr.cls())
                cname = raw_name.strip().lower().replace(" ", "_")      # 모델의 클래스 이름이 'Cigarette_butt'처럼 대문자여도 이벤트 종류가 일정하도록
                H, W = frame.shape[:2]
                x1, y1, x2, y2 = [int(v) for v in tr.box]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
                name = f"ID{n_events:04d}"
                d = out / "candidates" / name
                d.mkdir(parents=True, exist_ok=True)
                static = t - tr.anchor_t
                title = f"{name} auto {cname} conf={tr.conf_mean():.2f} bbox=({x1},{y1},{x2 - x1},{y2 - y1}) static={static:.1f}s"
                cv2.imwrite(str(d / "panel.jpg"), make_panel(frame, (x1, y1, x2, y2), title))
                cv2.imwrite(str(d / "frame.jpg"), frame)
                (d / "frame.txt").write_text(
                    f"{tr.cls()} {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} {(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}\n")
                ts = iso(start + timedelta(seconds=t)) if (start and not is_stream) else iso(datetime.now(timezone.utc))
                meta = {"id": n_events, "bbox_xywh": [x1, y1, x2 - x1, y2 - y1], "detected_at_sec": round(t, 2),
                        "static_sec": round(static, 2), "resolution": [W, H], "class": raw_name,
                        "confidence": round(tr.conf_mean(), 3), "auto_detected": True, "timestamp": ts}
                (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[감지] {name} {raw_name} conf={tr.conf_mean():.2f} 위치 {x1},{y1} 정지 {static:.1f}초")
                if srv:
                    frame_id = f"auto_{prefix}_{name}"
                    try:
                        sid = srv.snapshot(d / "panel.jpg", ts, frame_id)
                        msg = (f"자동 감지 {KO.get(cname, cname)} | 신뢰도 {tr.conf_mean():.2f} | 위치 {x1},{y1} | "
                               f"크기 {x2 - x1}x{y2 - y1}px | 정지 {static:.1f}초"
                               + (f" | 영상 {a.video_name}" if a.video_name else "") + f" | snap={sid}")
                        srv.post("events", {"timestamp": ts, "event_type": f"{cname}_detected", "severity": a.severity,
                                            "message": msg, "related_detection_id": frame_id, "snapshot_ref": sid})
                        uploaded[name] = {"snapshot_id": sid, "timestamp": ts, "frame_id": frame_id}
                        log_path.write_text(json.dumps(uploaded, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as e:                  # 서버 오류가 감지를 멈추지 않게 한다
                        print(f"[경고] 서버 전송 실패(폴더는 저장됨): {e}")

            if srv and a.heartbeat_sec > 0 and time.time() - last_hb >= a.heartbeat_sec:
                last_hb = time.time()
                try:
                    try:
                        import psutil
                        cpu, mem = float(psutil.cpu_percent()), float(psutil.virtual_memory().percent)
                    except Exception:
                        cpu = mem = 0.0
                    srv.post("heartbeat", {
                        "timestamp": iso(datetime.now(timezone.utc)), "status": "ok",
                        "agent": {"video_source": a.video_name or str(src), "processed_frames": n_proc,
                                  "cpu_percent": cpu, "mem_percent": mem},
                        "camera": {"connected": True, "fps": float(round(fps, 1))},
                        "inference": {"model_version": Path(a.model).name,
                                      "avg_latency_ms": int(lat_sum / max(1, n_proc) * 1000)}})
                except Exception as e:
                    print(f"[경고] heartbeat 실패: {e}")

            if a.save_video:
                vis = frame.copy()
                for d in dets:
                    cv2.rectangle(vis, (int(d[0]), int(d[1])), (int(d[2]), int(d[3])), (0, 255, 255), 1)
                for tr in tracker.tracks:
                    col = (0, 0, 255) if tr.emitted else (0, 200, 0)
                    cv2.rectangle(vis, (int(tr.box[0]), int(tr.box[1])), (int(tr.box[2]), int(tr.box[3])), col, 2)
                if writer is None:
                    writer = cv2.VideoWriter(str(out / "detect.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), a.proc_fps,
                                             (vis.shape[1], vis.shape[0]))
                writer.write(vis)
    except KeyboardInterrupt:
        print("\n중단합니다.")
    finally:
        cap.release()
        if writer:
            writer.release()

    wall = time.time() - wall0
    avg = lat_sum / max(1, n_proc) * 1000
    print(f"\n분석 {n_proc}프레임, 평균 처리 {avg:.0f}ms/프레임, 이벤트 {len(tracker.emitted)}건, 총 {wall:.0f}초")
    if a.gt or n_dets:
        ca = np.array(conf_all) if conf_all else np.array([0.0])
        print(f"[진단] 전체 검출 {n_dets}개 (프레임당 평균 {n_dets / max(1, n_proc):.2f}개), 신뢰도 최대 {ca.max():.2f}, 중앙값 {np.median(ca):.2f}")
    for gi in gt_items:
        rate = gi["hits"] / gi["frames"] * 100 if gi["frames"] else 0
        print(f"[진단] 정답 #{gi['id']} {gi['box']}: 정답 구간 {gi['frames']}프레임 중 {gi['hits']}프레임에서 근처 검출 ({rate:.0f}%), 최대 신뢰도 {gi['maxc']:.2f}")
    print(f"실시간 여유: 분석 간격 {1000 / a.proc_fps:.0f}ms 대비 처리 {avg:.0f}ms -> "
          + ("따라갈 수 있음" if avg < 1000 / a.proc_fps else "느림 (--proc-fps, --imgsz, --tile, --exclude-person 조정 필요)"))


if __name__ == "__main__":
    main()
