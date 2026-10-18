import sys
import time
import cv2
from pathlib import Path

# 모듈을 불러오기 위해 경로 설정 (demo.py와 동일한 방식)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import client
from agent.config import load

def main():
    # 1. 서버 주소 및 설정 불러오기
    cfg = load()
    c = client.EdgeClient(cfg)
    print(f"서버: {cfg.server_url} | 장치 ID: {cfg.device_id}")

    # 2. 노트북 기본 웹캠(0번) 켜기
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("에러: 웹캠을 찾을 수 없거나 실행할 수 없습니다!")
        return

    print("녹화 및 전송을 시작합니다... (종료하려면 터미널 창에서 Ctrl + C를 누르세요)")
    frame_idx = 0

    try:
        while True:
            # 3. 웹캠에서 사진 한 장 찍기
            ret, frame = cap.read()
            if not ret:
                continue

            frame_idx += 1
            frame_id = f"cam_00_{frame_idx:04d}"

            # 4. 이미지를 서버가 받을 수 있는 JPG 형식으로 변환
            success, buffer = cv2.imencode('.jpg', frame)
            if success:
                image_bytes = buffer.tobytes()

               # 5. 서버로 스냅샷 전송!
                try:
                    res = c.snapshot(image_bytes=image_bytes, frame_id=frame_id)
                    print(f"[{frame_id}] 서버 전송 성공 -> {res}")
                    
                    # 🌟 [여기에 아래 코드를 추가하세요] 대시보드 시간 업데이트용 상태 보고
                    c.heartbeat(
                        system={"cpu_percent": 15.0, "mem_percent": 40.0, "temp_celsius": 45.0, "uptime_seconds": 3600},
                        camera={"connected": True, "fps": 1.0},
                        inference={"model_version": "yolov8n-smoke-v1", "avg_latency_ms": 50},
                    )
                    
                except Exception as e:
                    print(f"[{frame_id}] 서버 전송 실패: {e}")

            # 서버 과부하 방지를 위해 1초에 한 장씩만 전송 (조절 가능)
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n프로그램을 안전하게 종료합니다.")
    finally:
        # 카메라 자원 해제
        cap.release()

if __name__ == "__main__":
    main()