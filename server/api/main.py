"""Central server for raspi-cctv-project (COMMUNICATION_PROTOCOL.md v0.1).

Run from the repo root:
    uvicorn server.api.main:app --reload --port 8000
    C:/Users/HP/Desktop/raspi-cctv-project/venv/Scripts/Activate.ps1

"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Path, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi import FastAPI, Depends, UploadFile, File, Path as ApiPath
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



def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message}},
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


# Pydantic validation errors -> 400 bad_request envelope.
from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError):
    return _error(400, "bad_request", exc.errors()[0]["msg"] if exc.errors() else "invalid payload")


# --- Auth dependency ---------------------------------------------------------
# Runs as part of dependency resolution so a bad/missing API key surfaces as
# 401 invalid_api_key (§5) instead of being shadowed by Pydantic body validation
# returning 400 bad_request.

def authed_device(
    device_id: str = ApiPath(...),
    x_device_api_key: Optional[str] = Header(default=None, alias="X-Device-Api-Key"),
) -> str:
    # require_api_key(device_id, x_device_api_key)
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

# (기존 코드) GET /events 바로 아래에 추가하세요!

@app.delete("/api/devices/{device_id}/events")
async def delete_events(device_id: str):
    # 장치가 존재하는지 확인
    if not storage.device_exists(device_id):
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
    
    # storage 모듈에 삭제 명령 전달
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


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    d = storage.get_device(device_id)
    if d is None:
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
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
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
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
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
    _check_limit(limit)
    return {"events": storage.query_events(device_id, since=since, until=until, limit=limit)}


# --- Candidate review (지속 변화 후보 검수: 대시보드 <-> 서버) ----------------------

@app.get("/api/devices/{device_id}/reviews")
async def get_reviews(device_id: str):
    if not storage.device_exists(device_id):
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
    return {"reviews": storage.list_reviews(device_id)}


@app.put("/api/devices/{device_id}/reviews/{candidate_id}")
async def put_review(
    candidate_id: str,
    body: ReviewIn,
    device_id: str = Depends(authed_device),
):
    if not storage.device_exists(device_id):
        raise HTTPException(404, detail={"code": "device_not_found", "message": f"device {device_id} not found"})
    return {"ok": True, "review": storage.set_review(device_id, candidate_id, body.label, body.note)}


@app.get("/api/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    s = storage.get_snapshot(snapshot_id)
    if s is None:
        raise HTTPException(404, detail={"code": "device_not_found", "message": "snapshot not found"})
    return Response(content=s["data"], media_type=s["content_type"])

_dashboard_dir = (
    Path(__file__).resolve().parent.parent / "static_dashboard"
)
if _dashboard_dir.exists():
  app.mount(
      "/",
      StaticFiles(directory=str(_dashboard_dir), html=True),
      name="dashboard",
  )