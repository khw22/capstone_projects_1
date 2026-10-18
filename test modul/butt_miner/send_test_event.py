"""
send_test_event.py - YOLO 모델 없이 '자동 감지 이벤트'를 서버에 보내, 대시보드에 어떻게 뜨는지 미리 확인

  python send_test_event.py --server http://localhost:8000 --device-id local-ai-server --cls litter
  python send_test_event.py --cls cigarette_butt --image 내패널.jpg

- detect_litter.py 가 자동 감지 때 보내는 것과 같은 형식(스냅샷 + cigarette_butt_detected / litter_detected 이벤트)이다.
- 메시지에 '(테스트)'가 붙어서 실제 감지와 구분된다. 서버 DB 에 저장되므로 필요 없으면 대시보드에서 지운다
  (주의: '화면 지우기'는 모든 이벤트와 검수 결과를 영구 삭제).
- 대시보드는 자동 새로고침이 없으므로 보낸 뒤 F5(또는 Ctrl+F5)를 눌러야 보인다.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import cv2
import numpy as np
import requests

KO = {"cigarette_butt": "꽁초", "litter": "쓰레기"}


def placeholder(cls):
    img = np.full((360, 640, 3), (70, 90, 70), np.uint8)
    cv2.rectangle(img, (250, 140), (390, 230), (0, 0, 255), 3)
    cv2.putText(img, f"TEST {cls}", (210, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(img, "auto-detect sample (no YOLO)", (170, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 255, 200), 1, cv2.LINE_AA)
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--device-id", default="local-ai-server")
    p.add_argument("--api-key", default=os.environ.get("DEVICE_API_KEY", ""))
    p.add_argument("--cls", choices=list(KO), default="litter")
    p.add_argument("--conf", type=float, default=0.62)
    p.add_argument("--image", default="", help="패널 이미지(없으면 안내용 그림을 만든다)")
    p.add_argument("--count", type=int, default=1, help="보낼 이벤트 개수")
    a = p.parse_args()

    base = a.server.rstrip("/")
    headers = {"X-Device-Api-Key": a.api_key} if a.api_key else {}
    try:
        ids = [d["device_id"] for d in requests.get(f"{base}/api/devices", timeout=5).json().get("devices", [])]
    except Exception as e:
        sys.exit(f"서버에 연결할 수 없습니다({base}): {e}")
    if a.device_id not in ids:
        sys.exit(f"등록되지 않은 장치입니다: {a.device_id}  (등록된 장치: {ids})")

    for i in range(a.count):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frame_id = f"auto_test_{datetime.now().strftime('%H%M%S')}_{i + 1}"
        if a.image:
            data = np.fromfile(a.image, dtype=np.uint8)
            ok, buf = True, data
        else:
            ok, buf = cv2.imencode(".jpg", placeholder(a.cls))
            buf = buf.tobytes()
        r = requests.post(f"{base}/api/devices/{a.device_id}/snapshots", headers=headers, timeout=20,
                          files={"file": ("panel.jpg", bytes(buf), "image/jpeg")},
                          data={"metadata": json.dumps({"timestamp": ts, "frame_id": frame_id})})
        r.raise_for_status()
        sid = r.json()["snapshot_id"]
        w, h = (60, 40) if a.cls == "litter" else (18, 11)
        msg = (f"자동 감지 {KO[a.cls]} (테스트) | 신뢰도 {a.conf:.2f} | 위치 {300 + 40 * i},{250 + 10 * i} | "
               f"크기 {w}x{h}px | 정지 10.3초 | snap={sid}")
        r = requests.post(f"{base}/api/devices/{a.device_id}/events", headers=headers, timeout=20,
                          json={"timestamp": ts, "event_type": f"{a.cls}_detected", "severity": "info", "message": msg,
                                "related_detection_id": frame_id, "snapshot_ref": sid})
        r.raise_for_status()
        print(f"[전송] {a.cls}_detected -> {sid}")
    print("대시보드에서 F5(Ctrl+F5)를 눌러 '꽁초 후보' 섹션과 '이벤트 기록'을 확인하세요.")


if __name__ == "__main__":
    main()
