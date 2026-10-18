"""Pydantic models for the protocol payloads (COMMUNICATION_PROTOCOL.md §4)."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Heartbeat ---------------------------------------------------------------

class AgentInfo(BaseModel):
    video_source: str       # 분석 중인 영상 소스 (예: test_fire.avi)
    processed_frames: int   # 현재까지 분석 완료한 누적 프레임 수
    cpu_percent: float      # 에이전트 구동 PC의 CPU 사용량 (유지)
    mem_percent: float      # 에이전트 구동 PC의 메모리 사용량 (유지)

class CameraInfo(BaseModel):
    connected: bool
    fps: float

class InferenceInfo(BaseModel):
    model_version: str
    avg_latency_ms: int

class HeartbeatIn(BaseModel):
    timestamp: str
    status: str
    agent: AgentInfo        # 기존 'system' 대신 'agent'로 명칭 및 구조 변경
    camera: CameraInfo
    inference: InferenceInfo


# --- Detection ---------------------------------------------------------------

class DetectedObject(BaseModel):
    # `class` is a Python keyword; expose as `class_` but accept/emit "class".
    model_config = ConfigDict(populate_by_name=True)

    class_: str = Field(alias="class")
    confidence: float
    bbox: List[int]


class OcrResult(BaseModel):
    text: str
    confidence: float
    bbox: List[int]


class Detection(BaseModel):
    timestamp: str
    frame_id: str
    objects: List[DetectedObject] = []
    ocr: List[OcrResult] = []
    snapshot_ref: Optional[str] = None


class DetectionsIn(BaseModel):
    detections: List[Detection]


# --- Event -------------------------------------------------------------------

class EventIn(BaseModel):
    timestamp: str
    event_type: str
    severity: Literal["info", "warning", "critical"]
    message: str
    related_detection_id: Optional[str] = None
    snapshot_ref: Optional[str] = None


# --- Candidate review (지속 변화 후보 검수) ----------------------------------------

class ReviewIn(BaseModel):
    # 후보 폴더 라벨 체계: 꽁초 / 기타 쓰레기 / 쓰레기 아님 / 불확실
    label: Literal["cigarette_butt", "other_litter", "not_litter", "uncertain"]
    note: Optional[str] = None


# --- Responses ---------------------------------------------------------------

class OkResponse(BaseModel):
    ok: bool = True
    received_at: Optional[str] = None


class AcceptedResponse(BaseModel):
    ok: bool = True
    accepted: int


class SnapshotResponse(BaseModel):
    ok: bool = True
    snapshot_id: str
    url: str


class DeviceListItem(BaseModel):
    device_id: str
    last_seen: Optional[str] = None
    status: Optional[str] = None
    location: Optional[str] = None
    # Surfaced from the latest heartbeat; null until one arrives.
    fps: Optional[float] = None
    model_version: Optional[str] = None


class DeviceListResponse(BaseModel):
    devices: List[DeviceListItem]
