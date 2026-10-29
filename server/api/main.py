"""Central server for raspi-cctv-project (COMMUNICATION_PROTOCOL.md v0.1).

Run from the repo root:
    uvicorn server.api.main:app --reload --port 8000
    C:/Users/HP/Desktop/raspi-cctv-project/venv/Scripts/Activate.ps1

cd C:/Users/HP/Desktop/capstone_project/test modul/butt_miner
python run_pipeline.py --video ../../영상파일이름.mp4 --out runs/demo --preset both --server http://localhost:8000

Environment variables:
    CCTV_REQUIRE_AUTH=1   -> enable X-Device-Api-Key check on ingest endpoints (default: off)
    CCTV_VIDEO_DIR=...    -> directory that /api/stream_video may read from (default: repo root)
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import requests  # 💡 텔레그램 API 통신용 패키지 (pip install requests)
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import storage
from .auth import require_api_key
from .models import (
    DetectionsIn,
    EventIn,
    HeartbeatIn,
    ReviewIn,
)

app = FastAPI(title="raspi-cctv central server", version="0.1.0")

# Static dashboard runs on a different origin (http://localhost:8000) during dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REQUIRE_AUTH = os.getenv("CCTV_REQUIRE_AUTH", "0") == "1"

# repo root = server/api/main.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_DIR = Path(os.getenv("CCTV_VIDEO_DIR", str(_REPO_ROOT))).resolve()
ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv"}

# --- 💡 텔레그램 알림 연동 설정 ---
# 토큰/채팅 ID 는 코드에 적지 말고 환경변수로 지정한다 (코드를 공유하거나 깃에 올려도 노출되지 않게).
#   PowerShell:  $env:CCTV_TELEGRAM_TOKEN="봇토큰"; $env:CCTV_TELEGRAM_CHAT_ID="채팅ID"
TELEGRAM_BOT_TOKEN = "8712296479:AAH8XJeyDKGmtC8Zf8u4jA3SQMd8pxLG8_4"
TELEGRAM_CHAT_ID = "8240192422"
_tg_warned = False
_last_tg_alert = {}  # 카메라별 연속 알림 스팸 방지용 타임스탬프

def send_telegram_fire_alert(device_id: str, frame) -> None:
    """화재 최초 감지 시 메모리에서 즉시 캡처하여 텔레그램으로 사진과 알림을 전송합니다."""
    global _tg_warned
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        if not _tg_warned:
            _tg_warned = True
            print("[stream] 텔레그램 알림 꺼짐: CCTV_TELEGRAM_TOKEN / CCTV_TELEGRAM_CHAT_ID 환경변수를 지정하세요.")
        return
    now = time.time()
    # 30초 이내에 동일 카메라에서 연속 알림이 울리는 스팸 방지
    if device_id in _last_tg_alert and (now - _last_tg_alert[device_id]) < 30:
        return
    _last_tg_alert[device_id] = now
    
    try:
        # 하드디스크 저장 과정 없이 메모리 상에서 바로 JPG 인코딩 (스트림 딜레이 방지)
        ret, buffer = cv2.imencode(".jpg", frame)
        if not ret:
            return
        
        time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        caption = (
            f"🔥 [긴급] 화재 감지 알림!\n\n"
            f"📍 위치: {device_id}\n"
            f"⏰ 일시: {time_str}\n\n"
            f"※ 즉시 관제 화면을 확인하세요!"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        
        # 텔레그램 서버로 사진 및 문구 전송
        resp = requests.post(
            url, 
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
            files={"photo": ("fire_snapshot.jpg", buffer.tobytes(), "image/jpeg")}, 
            timeout=5
        )
        if resp.ok:
            print(f"[stream] 📱 [{device_id}] 텔레그램 화재 사진 전송 완료!")
        else:  # 토큰/채팅 ID 오류 등은 예외가 아니라 HTTP 오류 코드로 오므로 직접 확인
            print(f"[stream] ❌ 텔레그램 전송 실패: HTTP {resp.status_code} {resp.text[:150]}")
    except Exception as e:
        # 오류 문구에 토큰이 들어간 주소가 섞여 나올 수 있어 가려서 출력
        print(f"[stream] ❌ 텔레그램 전송 실패: {str(e).replace(TELEGRAM_BOT_TOKEN, '***')}")

# --- AI 모델 (서버 시작 시 백그라운드에서 1회 로드) ---
FIRE_MODEL_PATH = Path(os.getenv("CCTV_FIRE_MODEL", str(_REPO_ROOT / "fire.pt")))
OCR_MODEL_PATH = Path(os.getenv("CCTV_OCR_MODEL", str(_REPO_ROOT / "ocr_detector.pt")))
FIRE_CONF = 0.15       # cctv_agent.py 와 동일
FIRE_TIMEOUT = 10.0    # 불꽃이 안 보인 뒤 테두리를 끄기까지 (초)
INFER_EVERY = 2        # N프레임마다 1회 추론 (1 = 매 프레임, 추적 정확도는 높지만 느림)
OCR_EVERY = 3.0        # OCR 실행 간격 (초)
OCR_DEDUP_SEC = 30.0   # 같은 판독값은 이 시간 안에 다시 기록하지 않음
OCR_DET_CONF = float(os.getenv("CCTV_OCR_DET_CONF", "0.5"))   # 숫자 영역 탐지 신뢰도
OCR_MIN_PROB = float(os.getenv("CCTV_OCR_MIN_PROB", "0.5"))   # 이 이상 확신한 판독값만 대시보드에 기록
OCR_ALLOWLIST = os.getenv("CCTV_OCR_ALLOWLIST", "0123456789:.-/%") or None
OCR_DEBUG = os.getenv("CCTV_OCR_DEBUG", "1") == "1"           # 판독 결과가 바뀔 때 터미널에 한 줄 출력
OCR_MIN_DIGITS = int(os.getenv("CCTV_OCR_MIN_DIGITS", "2"))   # 판독값에 숫자가 최소 이 개수는 있어야 인정
OCR_ENHANCE = os.getenv("CCTV_OCR_ENHANCE", "0") == "1"       # 1: 대비 보정(CLAHE) 버전도 읽어 더 확신 높은 결과 사용
SHOW_STATUS = os.getenv("CCTV_SHOW_STATUS", "0") == "1"          # 영상 위 평상시 "AI ANALYZING..." 문구 (기본 숨김)
SHOW_OCR_STATUS = os.getenv("CCTV_SHOW_OCR_STATUS", "0") == "1"  # 영상 위 "OCR: ON ..." 진단 문구 (기본 숨김)

_fire_model = None
_model_state = "loading"        # loading | ready | missing | error
_model_lock = threading.Lock()  # YOLO 추론/추적 직렬화 (스레드 안전하지 않음)
_ocr_model = None
_ocr_reader = None
_ocr_state = "loading"          # loading | ready | missing | error
_ocr_lock = threading.Lock()


def _load_models_bg() -> None:
    global _fire_model, _model_state, _ocr_model, _ocr_reader, _ocr_state
    import sys
    print(f"[stream] AI 모델 로딩 시작 (서버 Python: {sys.executable})")
    try:
        from ultralytics import YOLO
    except Exception as e:  # noqa: BLE001
        _model_state = _ocr_state = "error"
        print(f"[stream] ultralytics import failed: {e!r}")
        print("[stream] 이 Python 환경에 ultralytics 가 없습니다. cctv_agent.py 를 돌리던 가상환경을 활성화한 뒤 서버를 실행하세요.")
        return

    # 화재 모델 (필수)
    if not FIRE_MODEL_PATH.is_file():
        _model_state = "missing"
        print(f"[stream] fire model not found: {FIRE_MODEL_PATH}")
    else:
        try:
            print("[stream] 화재 모델 로딩/추적 초기화 중... (처음에는 패키지 자동 설치로 오래 걸릴 수 있음)")
            model = YOLO(str(FIRE_MODEL_PATH))
            import numpy as np
            model.track(np.zeros((64, 64, 3), np.uint8), persist=True, verbose=False)
            _reset_tracker(model)
            _fire_model = model
            _model_state = "ready"
            print(f"[stream] fire model loaded: {FIRE_MODEL_PATH}")
        except Exception as e:  # noqa: BLE001
            _model_state = "error"
            print(f"[stream] fire model load failed: {e!r}")
            print("[stream] 추적 패키지(lap 등)가 방금 설치됐다면 서버를 한 번 재시작하세요.")

    # OCR 모델 (선택: 없어도 화재 감지는 동작)
    if not OCR_MODEL_PATH.is_file():
        _ocr_state = "missing"
        print(f"[stream] ocr model not found (OCR disabled): {OCR_MODEL_PATH}")
    else:
        try:
            print("[stream] EasyOCR 로딩 중... (처음 실행이면 모델 다운로드로 몇 분 걸릴 수 있음)")
            import easyocr
            _ocr_model = YOLO(str(OCR_MODEL_PATH))
            _ocr_reader = easyocr.Reader(["en"])
            _ocr_state = "ready"
            print(f"[stream] ocr models loaded: {OCR_MODEL_PATH}")
        except Exception as e:  # noqa: BLE001
            _ocr_state = "error"
            print(f"[stream] ocr load failed (OCR disabled): {e!r}")
            print("[stream] easyocr 가 이 Python 환경에 설치돼 있는지 확인하세요: pip install easyocr")


@app.on_event("startup")
def _startup_load_model() -> None:
    threading.Thread(target=_load_models_bg, daemon=True).start()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


def _device_not_found(device_id: str) -> HTTPException:
    return HTTPException(
        404,
        detail={"code": "device_not_found", "message": f"device {device_id} not found"},
    )


# --- Error envelope (COMMUNICATION_PROTOCOL.md §5) ---------------------------

@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return _error(exc.status_code, exc.detail["code"], exc.detail.get("message", ""))
    code = {
        400: "bad_request",
        401: "invalid_api_key",
        404: "device_not_found",
        413: "payload_too_large",
        429: "rate_limited",
    }.get(exc.status_code, "server_error")
    return _error(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    return _error(500, "server_error", str(exc))


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError):
    return _error(400, "bad_request", exc.errors()[0]["msg"] if exc.errors() else "invalid payload")


# --- Auth dependency ---------------------------------------------------------

def authed_device(
    device_id: str = ApiPath(...),
    x_device_api_key: Optional[str] = Header(default=None, alias="X-Device-Api-Key"),
) -> str:
    if REQUIRE_AUTH:
        require_api_key(device_id, x_device_api_key)
    return device_id


# --- Ingest endpoints (edge → server) ---------------------------------------

@app.post("/api/devices/{device_id}/heartbeat")
async def post_heartbeat(
    body: HeartbeatIn,
    device_id: str = Depends(authed_device),
):
    storage.record_heartbeat(device_id, body.model_dump())
    return {"ok": True, "received_at": _now_iso()}


@app.post("/api/devices/{device_id}/detections")
async def post_detections(
    body: DetectionsIn,
    device_id: str = Depends(authed_device),
):
    items = [d.model_dump(by_alias=True) for d in body.detections]
    accepted = storage.append_detections(device_id, items)
    return {"ok": True, "accepted": accepted}


@app.post("/api/devices/{device_id}/events")
async def post_event(
    body: EventIn,
    device_id: str = Depends(authed_device),
):
    storage.append_event(device_id, body.model_dump())
    return {"ok": True}


@app.delete("/api/devices/{device_id}/events")
async def delete_events(device_id: str):
    if not storage.device_exists(device_id):
        raise _device_not_found(device_id)

    storage.clear_all_events(device_id)
    return {"ok": True, "message": "All events completely deleted"}


@app.post("/api/devices/{device_id}/snapshots")
async def post_snapshot(
    file: UploadFile = File(...),
    metadata: str = Form(...),
    device_id: str = Depends(authed_device),
):
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(400, detail={"code": "bad_request", "message": "metadata is not valid JSON"})
    data = await file.read()
    snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
    storage.store_snapshot(
        device_id=device_id,
        snapshot_id=snapshot_id,
        data=data,
        metadata=meta,
        content_type=file.content_type or "application/octet-stream",
    )
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "url": f"/api/snapshots/{snapshot_id}",
    }


# --- Query endpoints (dashboard → server) -----------------------------------

@app.get("/api/devices")
async def get_devices():
    return {"devices": storage.list_devices()}


@app.get("/api/cameras")
async def get_cameras():
    if CAMERAS_FILE.is_file():
        try:
            data = json.loads(CAMERAS_FILE.read_text(encoding="utf-8-sig"))
            return {"cameras": data.get("cameras", [])}
        except Exception as e:  # noqa: BLE001
            print(f"[stream] cameras.json 읽기 실패: {e!r}")
    return {"cameras": []}


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    d = storage.get_device(device_id)
    if d is None:
        raise _device_not_found(device_id)
    return d


def _check_limit(limit: int) -> None:
    if limit < 1 or limit > 1000:
        raise HTTPException(400, detail={"code": "bad_request", "message": "limit must be between 1 and 1000"})


@app.get("/api/devices/{device_id}/detections")
async def get_detections(
    device_id: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
):
    if not storage.device_exists(device_id):
        raise _device_not_found(device_id)
    _check_limit(limit)
    return {"detections": storage.query_detections(device_id, since=since, until=until, limit=limit)}


@app.get("/api/devices/{device_id}/events")
async def get_events(
    device_id: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
):
    if not storage.device_exists(device_id):
        raise _device_not_found(device_id)
    _check_limit(limit)
    return {"events": storage.query_events(device_id, since=since, until=until, limit=limit)}


# --- Candidate review (지속 변화 후보 검수: 대시보드 <-> 서버) ----------------------

@app.get("/api/devices/{device_id}/reviews")
async def get_reviews(device_id: str):
    if not storage.device_exists(device_id):
        raise _device_not_found(device_id)
    return {"reviews": storage.list_reviews(device_id)}


@app.put("/api/devices/{device_id}/reviews/{candidate_id}")
async def put_review(
    candidate_id: str,
    body: ReviewIn,
    device_id: str = Depends(authed_device),
):
    if not storage.device_exists(device_id):
        raise _device_not_found(device_id)
    return {"ok": True, "review": storage.set_review(device_id, candidate_id, body.label, body.note)}


@app.get("/api/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    s = storage.get_snapshot(snapshot_id)
    if s is None:
        raise HTTPException(404, detail={"code": "device_not_found", "message": "snapshot not found"})
    return Response(content=s["data"], media_type=s["content_type"])


# --- Video Streaming Endpoint (지도 관제 모달창 송출용) ---

STREAM_EVENTS = os.getenv("CCTV_STREAM_EVENTS", "1") == "1"
STREAM_DEVICE_ID = os.getenv("CCTV_STREAM_DEVICE_ID", "local-ai-server")
EVENT_INTERVAL = 5.0     
SIZE_CHANGE_TH = 1000    
MOVE_TH = 50             
_storage_lock = threading.Lock()


def _emit_event(device_id: str, timestamp: str, event_type: str, severity: str, message: str) -> None:
    try:
        payload = EventIn(
            timestamp=timestamp, event_type=event_type, severity=severity, message=message
        ).model_dump()
        with _storage_lock:
            storage.append_event(device_id, payload)
    except Exception as e:  # noqa: BLE001
        print(f"[stream] event save failed: {e!r}")


CMD_POLL_SEC = 1.0       
STREAM_SNAPSHOTS = os.getenv("CCTV_STREAM_SNAPSHOTS", "1") == "1"  

try:
    import psutil
    _proc = psutil.Process(os.getpid())
    _ncpu = psutil.cpu_count() or 1
except Exception:  # noqa: BLE001
    psutil = None
    _proc = None
    _ncpu = 1


def _emit_heartbeat(device_id: str, video: str, frames: int, fps: float, latency_ms: int, timestamp: str) -> None:
    try:
        cpu = _proc.cpu_percent(interval=None) / _ncpu if _proc else 0.0
        mem_mb = _proc.memory_info().rss / (1024 * 1024) if _proc else 0.0
        payload = HeartbeatIn(
            timestamp=timestamp,
            status="ok",
            agent={"video_source": video, "processed_frames": frames,
                   "cpu_percent": round(cpu, 1), "mem_percent": round(mem_mb, 1)},
            camera={"connected": True, "fps": round(fps, 1)},
            inference={"model_version": "yolov8-fire-v2", "avg_latency_ms": latency_ms},
        ).model_dump()
        with _storage_lock:
            storage.record_heartbeat(device_id, payload)
    except Exception as e:  # noqa: BLE001
        print(f"[stream] heartbeat save failed: {e!r}")


def _store_snapshot(device_id: str, jpeg: bytes, frame_idx: int, source: str) -> None:
    try:
        snap_id = f"video_{frame_idx:05d}_{uuid.uuid4().hex[:6]}"
        with _storage_lock:
            storage.store_snapshot(
                device_id=device_id,
                snapshot_id=snap_id,
                data=jpeg,
                metadata={"frame": frame_idx, "source": source},
                content_type="image/jpeg",
            )
    except Exception as e:  # noqa: BLE001
        print(f"[stream] snapshot save failed: {e!r}")


def _manual_off_since(device_id: str, since_ts: float) -> float:
    with _storage_lock:
        recent = storage.query_events(device_id, since=None, until=None, limit=5)
    latest = 0.0
    for e in recent:
        if e.get("message") == "MANUAL_ALARM_OFF":
            ts = datetime.fromisoformat(str(e["timestamp"]).replace("Z", "+00:00")).timestamp()
            if ts > since_ts:
                latest = max(latest, ts)
    return latest


CAMERAS_FILE = Path(os.getenv(
    "CCTV_CAMERAS_FILE",
    str(Path(__file__).resolve().parent.parent / "static_dashboard" / "cameras.json"),
))
_CAMERAS: dict = {}          
_CAMERA_ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _load_cameras() -> None:
    _CAMERAS.clear()
    if not CAMERAS_FILE.is_file():
        print(f"[stream] cameras.json 이 없습니다 (CCTV 번호 기능 비활성): {CAMERAS_FILE}")
        return
    try:
        data = json.loads(CAMERAS_FILE.read_text(encoding="utf-8-sig"))
        for cam in data.get("cameras", []):
            cid = str(cam.get("id", "")).strip()
            if not _CAMERA_ID_OK.match(cid):
                print(f"[stream] 건너뜀: CCTV id 는 영문/숫자/-/_ 만 가능합니다: {cid!r}")
                continue
            entry = {
                "id": cid,
                "name": str(cam.get("name", "")),
                "lat": cam.get("lat"),
                "lng": cam.get("lng"),
                "video": cam.get("video"),
            }
            _CAMERAS[cid] = entry
            key = storage.get_api_key(cid) or f"dev-key-{cid.lower()}"
            storage.register_device(cid, key, entry["name"] or cid)
        print(f"[stream] CCTV 등록: {', '.join(_CAMERAS) or '(없음)'}")
    except Exception as e:  # noqa: BLE001
        print(f"[stream] cameras.json 읽기 실패: {e!r}")


@app.on_event("startup")
def _startup_register_cameras() -> None:
    _load_cameras()


def _resolve_video(filename: str) -> Path:
    candidate = (VIDEO_DIR / filename).resolve()
    try:
        candidate.relative_to(VIDEO_DIR)
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_request", "message": "invalid filename"})
    if candidate.suffix.lower() not in ALLOWED_VIDEO_EXT:
        raise HTTPException(400, detail={"code": "bad_request", "message": "unsupported video type"})
    if not candidate.is_file():
        raise HTTPException(404, detail={"code": "not_found", "message": "video not found"})
    return candidate


def _reset_tracker(model) -> None:
    try:
        pred = getattr(model, "predictor", None)
        for t in (getattr(pred, "trackers", None) or []):
            if hasattr(t, "reset"):
                t.reset()
    except Exception as e:  # noqa: BLE001
        print(f"[stream] tracker reset skipped: {e!r}")


def _drop_nested(dets):
    kept = []
    for d in sorted(dets, key=lambda d: (d[2] - d[0]) * (d[3] - d[1]), reverse=True):
        area = max((d[2] - d[0]) * (d[3] - d[1]), 1)
        nested = False
        for k in kept:
            ix = max(0, min(d[2], k[2]) - max(d[0], k[0]))
            iy = max(0, min(d[3], k[3]) - max(d[1], k[1]))
            if ix * iy / area >= 0.8:
                nested = True
                break
        if not nested:
            kept.append(d)
    return kept


def _read_crop(crop):
    def read(img):
        valid, dropped = [], []
        for (_, text, prob) in _ocr_reader.readtext(img, allowlist=OCR_ALLOWLIST):
            text = text.strip()
            if not text:
                continue
            if sum(ch.isdigit() for ch in text) >= OCR_MIN_DIGITS:
                valid.append((text, float(prob)))
            else:
                dropped.append(f"{text}({prob:.2f})")
        return valid, dropped

    best, dropped = read(crop)
    if OCR_ENHANCE:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        enhanced = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(gray)
        alt, alt_dropped = read(enhanced)

        def score(rs):
            return sum(p for _, p in rs) / len(rs) if rs else 0.0
        if score(alt) > score(best):
            best, dropped = alt, alt_dropped
    return best, dropped


def _ocr_job(device_id: str, frame, busy: threading.Event, sent: dict, view: dict) -> None:
    try:
        found = []            
        shown = []            
        reads = []            
        dropped = []          
        h, w = frame.shape[:2]
        with _ocr_lock:
            dets = []
            for r in _ocr_model(frame, verbose=False, conf=OCR_DET_CONF):
                for ob in r.boxes:
                    x1, y1, x2, y2 = map(int, ob.xyxy[0].tolist())
                    dets.append((x1, y1, x2, y2))
            n_boxes = len(dets)
            for (x1, y1, x2, y2) in _drop_nested(dets):
                pad = 8
                crop = frame[max(y1 - pad, 0):min(y2 + pad, h), max(x1 - pad, 0):min(x2 + pad, w)]
                if crop.size == 0:
                    continue
                if crop.shape[0] < 64:  
                    scale = 64.0 / crop.shape[0]
                    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                valid, drop = _read_crop(crop)
                dropped.extend(drop)
                parts = []
                for text, prob in valid:
                    reads.append(f"{text}({prob:.2f})")
                    parts.append(f"{text} {prob:.2f}")
                    if prob >= OCR_MIN_PROB:
                        found.append(text)
                shown.append((x1, y1, x2, y2, " | ".join(parts)))
        view["boxes"] = shown
        view["summary"] = f"boxes={n_boxes} reads={len(reads)}"
        sig = f"det={n_boxes} reads={reads} dropped={dropped}"
        if OCR_DEBUG and sig != view.get("sig"):
            view["sig"] = sig
            print(f"[stream] OCR {sig}")

        now = time.time()
        iso = _now_iso()
        for text in found:
            if now - sent.get(text, 0.0) >= OCR_DEDUP_SEC:
                sent[text] = now
                _emit_event(device_id, iso, "ocr_reading", "info", f"판독 수치: {text}")
    except Exception as e:  # noqa: BLE001
        view["summary"] = "error (see server log)"
        print(f"[stream] ocr failed: {e!r}")
    finally:
        busy.clear()


@app.get("/api/stream_video")
def stream_video(filename: Optional[str] = None, camera: Optional[str] = None, loop: bool = False):
    device_id = STREAM_DEVICE_ID
    if camera:  
        cam = None
        if CAMERAS_FILE.is_file():
            try:
                data = json.loads(CAMERAS_FILE.read_text(encoding="utf-8-sig"))
                for c in data.get("cameras", []):
                    if str(c.get("id", "")).strip() == camera:
                        cam = c
                        break
            except Exception as e:  # noqa: BLE001
                print(f"[stream] cameras.json 읽기 실패: {e!r}")

        if cam is None:
            raise HTTPException(404, detail={"code": "device_not_found", "message": f"camera {camera} not found"})
        device_id = cam["id"]
        filename = filename or cam.get("video")

    if not filename:
        raise HTTPException(400, detail={"code": "bad_request", "message": "filename or camera is required"})
    video_path = _resolve_video(filename)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise HTTPException(500, detail={"code": "server_error", "message": "cannot open video"})

    fps = cap.get(cv2.CAP_PROP_FPS)
    delay = 1.0 / fps if fps and fps > 0 else 0.033

    def generate_frames():
        boxes = []            
        last_fire_time = 0.0
        frame_idx = 0
        infer_failed = False
        just_rewound = False
        last_emit = 0.0
        last_ocr = time.time()
        last_cmd_check = 0.0
        last_command_time = time.time()
        cmd_err_logged = False
        muted = False         
        snap_due = False
        win_start = time.time()
        win_frames = 0
        infer_sum = 0
        infer_n = 0
        event_active = False
        fire_origins = {}     
        last_size, last_x, last_y = 0, 0, 0
        ocr_busy = threading.Event()
        ocr_sent = {}
        ocr_view = {"boxes": [], "summary": "waiting for first scan"}
        tracker_reset = False
        try:
            while cap.isOpened():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok:
                    if loop and not just_rewound:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        boxes = []
                        fire_origins.clear()
                        event_active = False
                        last_fire_time = 0.0
                        just_rewound = True
                        continue
                    break  
                just_rewound = False

                frame_idx += 1
                win_frames += 1
                now = time.time()
                model = _fire_model if _model_state == "ready" else None

                if STREAM_EVENTS and now - last_cmd_check >= CMD_POLL_SEC:
                    last_cmd_check = now
                    try:
                        cmd_ts = _manual_off_since(device_id, last_command_time)
                        if cmd_ts > 0:
                            muted = True
                            last_command_time = cmd_ts
                    except Exception as e:  # noqa: BLE001
                        if not cmd_err_logged:
                            cmd_err_logged = True
                            print(f"[stream] remote command check failed: {e!r}")

                if model is not None and not tracker_reset:
                    with _model_lock:
                        _reset_tracker(model)
                    tracker_reset = True

                if model is not None and not infer_failed and frame_idx % INFER_EVERY == 0:
                    try:
                        ti = time.time()
                        with _model_lock:
                            res = model.track(frame, persist=True, verbose=False,
                                              conf=FIRE_CONF, imgsz=640)[0]
                        infer_sum += int((time.time() - ti) * 1000)
                        infer_n += 1
                        boxes = []
                        for b in res.boxes:
                            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                            tid = int(b.id[0]) if b.id is not None else 0
                            label = f"#{tid} {res.names[int(b.cls[0])]} {float(b.conf[0]):.2f}"
                            boxes.append((x1, y1, x2, y2, label, tid))
                        if boxes:
                            last_fire_time = now
                    except Exception as e:  # noqa: BLE001
                        infer_failed = True
                        boxes = []
                        print(f"[stream] inference failed: {e!r}")

                fire_active = last_fire_time > 0 and (now - last_fire_time) <= FIRE_TIMEOUT
                if muted and not fire_active:
                    muted = False  

                if (STREAM_EVENTS and _ocr_state == "ready" and not ocr_busy.is_set()
                        and now - last_ocr >= OCR_EVERY):
                    last_ocr = now
                    ocr_busy.set()
                    threading.Thread(target=_ocr_job, args=(device_id, frame.copy(), ocr_busy, ocr_sent, ocr_view),
                                     daemon=True).start()

                if STREAM_EVENTS and now - last_emit >= EVENT_INTERVAL:
                    iso = _now_iso()
                    elapsed = max(now - win_start, 1e-6)
                    avg_latency = int(infer_sum / infer_n) if infer_n else 0
                    _emit_heartbeat(device_id, filename, frame_idx, win_frames / elapsed, avg_latency, iso)
                    win_start, win_frames, infer_sum, infer_n = now, 0, 0, 0
                    last_emit = now
                    snap_due = STREAM_SNAPSHOTS

                    if boxes:
                        x1, y1, x2, y2, _, tid = boxes[0]
                        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                        size = (x2 - x1) * (y2 - y1)
                        if tid not in fire_origins:
                            fire_origins[tid] = (cx, cy)
                        ox, oy = fire_origins[tid]
                        _emit_event(device_id, iso, "fire_tracking",
                                    "critical" if size >= 10000 else "warning",
                                    f"[화염-{tid}] 발화점(X:{ox}, Y:{oy}) ➡️ 현재(X:{cx}, Y:{cy}) | 크기: {size}px")

                        if not event_active:
                            _emit_event(device_id, iso, "화재", "critical", "🔥 화재 최초 발생 감지!")
                            event_active = True
                            last_size, last_x, last_y = size, cx, cy
                            
                            # 💡 화재 최초 발생 순간, 텔레그램 스레드를 비동기로 실행하여 사진 전송
                            threading.Thread(
                                target=send_telegram_fire_alert, 
                                args=(device_id, frame.copy()), 
                                daemon=True
                            ).start()

                        else:
                            moved = abs(cx - last_x) > MOVE_TH or abs(cy - last_y) > MOVE_TH
                            size_changed = abs(size - last_size) > SIZE_CHANGE_TH
                            if moved or size_changed:
                                _emit_event(device_id, iso, "화재", "critical",
                                            f"⚠️ 화재 확산/이동 감지 (X={cx}, Y={cy})")
                                last_size, last_x, last_y = size, cx, cy
                    else:
                        fire_origins.clear()
                        if event_active and now - last_fire_time > FIRE_TIMEOUT:
                            _emit_event(device_id, iso, "안내", "info", "✅ 완전 소진 및 화재 상황 종료")
                            event_active = False

                for x1, y1, x2, y2, label, _ in boxes:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    cv2.putText(frame, label, (x1, max(y1 - 8, 15)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                for x1, y1, x2, y2, label in ocr_view["boxes"]:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)
                    if label:
                        cv2.putText(frame, label, (x1, max(y1 - 8, 15)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

                if fire_active and not muted and int(now * 2) % 2 == 0:
                    h, w = frame.shape[:2]
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 255), 30)
                    cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

                if infer_failed or _model_state == "error":
                    status, color = "MODEL ERROR (see server log)", (0, 165, 255)
                elif _model_state == "missing":
                    status, color = "MODEL NOT FOUND", (0, 165, 255)
                elif _model_state == "loading":
                    status, color = "MODEL LOADING...", (0, 255, 255)
                elif fire_active:
                    status, color = None, None
                else:
                    status, color = ("AI ANALYZING...", (0, 255, 0)) if SHOW_STATUS else (None, None)
                if status:
                    cv2.putText(frame, status, (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 3)

                if not STREAM_EVENTS:
                    ocr_txt, ocr_col = "OCR: OFF (CCTV_STREAM_EVENTS=0)", (180, 180, 180)
                elif _ocr_state == "ready":
                    ocr_txt, ocr_col = f"OCR: ON  {ocr_view['summary']}", (255, 255, 0)
                elif _ocr_state == "loading":
                    ocr_txt, ocr_col = "OCR: LOADING (first run downloads EasyOCR models)", (0, 255, 255)
                elif _ocr_state == "missing":
                    ocr_txt, ocr_col = "OCR: MODEL FILE NOT FOUND", (0, 165, 255)
                else:
                    ocr_txt, ocr_col = "OCR: LOAD FAILED (see server log)", (0, 165, 255)
                if SHOW_OCR_STATUS:
                    cv2.putText(frame, ocr_txt, (30, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ocr_col, 2)

                ret, buffer = cv2.imencode(".jpg", frame)
                if not ret:
                    continue
                jpeg = buffer.tobytes()

                if snap_due:
                    snap_due = False
                    _store_snapshot(device_id, jpeg, frame_idx, filename)

                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")

                elapsed_f = time.time() - t0
                if elapsed_f < delay:
                    time.sleep(delay - elapsed_f)
        finally:
            cap.release()

    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# --- Static dashboard ---
_dashboard_dir = Path(__file__).resolve().parent.parent / "static_dashboard"
if _dashboard_dir.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(_dashboard_dir), html=True),
        name="dashboard",
    )