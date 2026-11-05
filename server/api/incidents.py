import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 처리 이력을 저장할 파일
INCIDENTS_FILE = Path("incidents.json")

def _load():
    if not INCIDENTS_FILE.exists(): return []
    try:
        return json.loads(INCIDENTS_FILE.read_text(encoding="utf-8"))
    except:
        return []

def _save(data):
    INCIDENTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def create_incident(device_id: str, snapshot_url: str):
    data = _load()
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inc = {
        "id": f"inc_{uuid.uuid4().hex[:8]}",
        "device_id": device_id,
        "status": "발생", # 상태: 발생 -> 확인중 -> 조치완료
        "assignee": "",
        "created_at": iso,
        "updated_at": iso,
        "snapshot_url": snapshot_url
    }
    data.insert(0, inc)
    _save(data)
    return inc

def get_incidents():
    return _load()

def update_incident(incident_id: str, status: str = None, assignee: str = None):
    data = _load()
    for inc in data:
        if inc["id"] == incident_id:
            if status: inc["status"] = status
            if assignee is not None: inc["assignee"] = assignee
            inc["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _save(data)
            return inc
    return None

# 기존 함수들 밑에 아래 함수들을 추가합니다.

def delete_incident(incident_id: str):
    data = _load()
    new_data = [inc for inc in data if inc["id"] != incident_id]
    if len(new_data) == len(data):
        return False
    _save(new_data)
    return True

def clear_all_incidents():
    _save([])
    return True