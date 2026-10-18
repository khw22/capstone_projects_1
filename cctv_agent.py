import sys
import os
import threading
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'edge-device'))

import cv2
import time
import requests
import psutil
from datetime import datetime, timezone
from ultralytics import YOLO
from agent.client import EdgeClient
from agent.config import load
import easyocr

def send_api_async(url: str, payload: dict):
    def _send():
        try:
            requests.post(url, json=payload, timeout=2.0)
        except Exception as e:
            pass 
    threading.Thread(target=_send, daemon=True).start()

def run_video_pipeline(video_path: str, interval: float = 5.0):
    cfg = load()
    client = EdgeClient(cfg)
    
    SERVER_URL = "http://localhost:8000"
    DEVICE_ID = "local-ai-server"
    
    print("[*] 화재 감지 AI 모델(fire.pt)을 불러오는 중입니다...")
    model = YOLO('fire.pt') 

    # 💡 [추가] OCR용 YOLO 모델과 EasyOCR 리더기 로드
    ocr_model = YOLO('ocr_detector.pt')
    reader = easyocr.Reader(['en'])
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[!] '{video_path}' 동영상을 열 수 없습니다.")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    delay = 1 / fps if fps > 0 else 0.033

    print(f"[*] 동영상({video_path}) 분석 시작")

    frame_count = 0
    last_send_time = time.time()
    fire_origins = {} 
    process = psutil.Process(os.getpid())

    # ==========================================
    # 💡 [신규] 연속 경보 & 완전 소진 타이머 변수
    # ==========================================
    show_red_border = False
    alarm_muted_until_new_fire = False
    last_fire_time = 0
    FIRE_TIMEOUT = 10.0  # 🔥 불꽃이 안 보인지 10초가 지나면 "완전 소진"으로 판단!
    last_command_time = time.time()

    is_fire_active = False       
    last_event_time = 0          
    last_fire_size = 0           
    last_x, last_y = 0, 0        
    EVENT_INTERVAL = 3.0         
    SIZE_CHANGE_TH = 1000        
    MOVE_TH = 50                 

    # 대시보드에서 보낸 [끄기] 명령을 백그라운드에서 확인하는 함수
    def check_remote_commands():
        nonlocal show_red_border, alarm_muted_until_new_fire, last_command_time
        try:
            res = requests.get(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events?limit=3", timeout=2.0)
            if res.status_code == 200:
                for e in res.json().get("events", []):
                    if e.get("message") == "MANUAL_ALARM_OFF":
                        ts_str = e.get("timestamp").replace("Z", "+00:00")
                        event_ts = datetime.fromisoformat(ts_str).timestamp()
                        if event_ts > last_command_time:
                            print("[*] 🔕 대시보드 원격 명령: CCTV 영상 테두리 경보 해제!")
                            show_red_border = False
                            alarm_muted_until_new_fire = True # 다음 번 완전히 꺼질 때까지 알람 음소거
                            last_command_time = event_ts
        except:
            pass

    while True:
        start_time = time.time()

        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            fire_origins.clear() 
            is_fire_active = False 
            show_red_border = False
            alarm_muted_until_new_fire = False
            continue
            
        frame_count += 1
        
        results = model.track(frame, persist=True, verbose=False, conf=0.15, imgsz=640)
        annotated_frame = results[0].plot() 
        current_time = time.time()
        boxes = results[0].boxes
        
        # ==========================================
        # 💡 [신규] 끊기지 않는 테두리 및 소진 판단 로직
        # ==========================================
        if len(boxes) > 0:
            last_fire_time = current_time # 마지막으로 불이 보인 시간 갱신
            if not alarm_muted_until_new_fire:
                show_red_border = True # 한 번이라도 보이면 테두리 켬
        else:
            # 불꽃이 안 보인 지 10초(FIRE_TIMEOUT)가 넘었다면? -> 완전 소진
            if current_time - last_fire_time > FIRE_TIMEOUT:
                if show_red_border or alarm_muted_until_new_fire:
                    print("[✅] 10초 연속 화재 미감지 -> 완전 소진 판단 (알람 초기화)")
                    show_red_border = False
                    alarm_muted_until_new_fire = False # 뮤트 해제 (새 화재 시 다시 알람)

        # 🚨 빨간색 반투명 테두리 적용 (0.5초 간격 깜빡임 효과)
        if show_red_border:
            # 💡 현재 시간(current_time)을 이용해 0.5초 단위로 켜짐/꺼짐을 반복합니다.
            if int(current_time * 2) % 2 == 0:
                h, w = annotated_frame.shape[:2]
                overlay = annotated_frame.copy()
                cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 255), 30)
                cv2.addWeighted(overlay, 0.4, annotated_frame, 0.6, 0, annotated_frame)
            
        cv2.imshow("Capstone Fire Detection", annotated_frame)
        
        # 5초(interval) 마다 실행되는 블록
        if current_time - last_send_time >= interval:
            # 💡 5초마다 대시보드에서 끄기 버튼 눌렀는지 확인
            threading.Thread(target=check_remote_commands, daemon=True).start()

            current_cpu = process.cpu_percent(interval=None) / psutil.cpu_count()
            current_mem_mb = process.memory_info().rss / (1024 * 1024)
            latency_ms = int((time.time() - start_time) * 1000)
            current_fps = 1000 / latency_ms if latency_ms > 0 else 0
            iso_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            heartbeat_payload = {
                "timestamp": iso_timestamp, "status": "ok",
                "agent": { "video_source": video_path, "processed_frames": frame_count, "cpu_percent": round(current_cpu, 1), "mem_percent": round(current_mem_mb, 1) },
                "camera": { "connected": True, "fps": round(current_fps, 1) },
                "inference": { "model_version": "yolov8-fire-v2", "avg_latency_ms": latency_ms }
            }
            print(f"[*] 하트비트(5초) -> CPU: {current_cpu:.1f}%, RAM: {current_mem_mb:.1f} MB | 속도: {latency_ms}ms, FPS: {current_fps:.1f}")
            send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/heartbeat", heartbeat_payload)

            if len(boxes) > 0: 
                box = boxes.xywh[0]
                track_id = int(boxes.id[0]) if boxes.id is not None else 0 
                current_x, current_y = int(box[0]), int(box[1])
                current_size = int(box[2]) * int(box[3])
                
                if track_id not in fire_origins:
                    fire_origins[track_id] = (current_x, current_y)
                
                origin_x, origin_y = fire_origins[track_id]
                tracking_msg = f"[화염-{track_id}] 발화점(X:{origin_x}, Y:{origin_y}) ➡️ 현재(X:{current_x}, Y:{current_y}) | 크기: {current_size}px"
                
                send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events", {
                    "timestamp": iso_timestamp, "event_type": "fire_tracking", 
                    "severity": "critical" if current_size >= 10000 else "warning", "message": tracking_msg
                })

                if not is_fire_active:
                    send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events", {
                        "timestamp": iso_timestamp, "event_type": "화재", "severity": "critical", "message": "🔥 화재 최초 발생 감지!"
                    })
                    is_fire_active = True
                    last_event_time = current_time
                    last_fire_size, last_x, last_y = current_size, current_x, current_y
                else:
                    moved = abs(current_x - last_x) > MOVE_TH or abs(current_y - last_y) > MOVE_TH
                    size_changed = abs(current_size - last_fire_size) > SIZE_CHANGE_TH
                    
                    if moved or size_changed:
                        send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events", {
                            "timestamp": iso_timestamp, "event_type": "화재", "severity": "critical", "message": f"⚠️ 화재 확산/이동 감지 (X={current_x}, Y={current_y})"
                        })
                        last_event_time, last_fire_size, last_x, last_y = current_time, current_size, current_x, current_y
            else:
                fire_origins.clear()
                if is_fire_active and current_time - last_fire_time > FIRE_TIMEOUT:
                    send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events", {
                        "timestamp": iso_timestamp, "event_type": "안내", "severity": "info", "message": "✅ 완전 소진 및 화재 상황 종료"
                    })
                    is_fire_active = False

                    # 💡 [추가] 5초마다 화면 내 기기 숫자를 읽어 대시보드로 전송
            ocr_results = ocr_model(frame, verbose=False, conf=0.5)
            for r in ocr_results:
                for ocr_box in r.boxes:
                    x1, y1, x2, y2 = map(int, ocr_box.xyxy[0])
                    cropped_img = frame[y1:y2, x1:x2]
                    
                    if cropped_img.size != 0:
                        texts = reader.readtext(cropped_img)
                        for (bbox, text, prob) in texts:
                            if prob > 0.5: # 신뢰도가 50% 이상일 때만 전송
                                send_api_async(f"{SERVER_URL}/api/devices/{DEVICE_ID}/events", {
                                    "timestamp": iso_timestamp, 
                                    "event_type": "ocr_reading", 
                                    "severity": "info", 
                                    "message": f"판독 수치: {text}"
                                })

            success, buffer = cv2.imencode(".jpg", annotated_frame)
            if success:
                frame_id = f"video_{frame_count:05d}"
                threading.Thread(target=lambda b, f: [client.snapshot(b, frame_id=f) for _ in [1]], args=(buffer.tobytes(), frame_id), daemon=True).start()
                    
            last_send_time = current_time

        process_time = time.time() - start_time
        if process_time < delay:
            time.sleep(delay - process_time)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    video_file = "fire1.mp4"
    run_video_pipeline(video_file, interval=5.0)