import sys
import os
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'edge-device'))

import pymupdf as fitz
import cv2
import numpy as np
import time
from ultralytics import YOLO  # 👈 YOLO AI 라이브러리 추가
from agent.client import EdgeClient
from agent.config import load

def run_yolo_pipeline(pdf_path: str, interval: float = 1.0):
    cfg = load()
    client = EdgeClient(cfg)
    
    # 1. YOLOv8 모델 로드 (처음 실행 시 가벼운 기본 모델 자동 다운로드)
    print("[*] YOLO AI 모델을 불러오는 중입니다...")
    model = YOLO('yolov8n.pt') 
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print(f"[*] PDF 로드 완료: {pdf_path} (총 {total_pages} 페이지)")

    for page_num in range(total_pages):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=72)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        
        if pix.n == 4:
            frame = cv2.cvtColor(img_array, cv2.COLOR_RGBA2BGR)
        else:
            frame = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

        # 2. [핵심] 이미지에 AI 감지 실행 및 네모 박스 그리기
        results = model(frame, verbose=False)
        annotated_frame = results[0].plot() 
        
        # 3. AI가 분석한 화면을 서버로 전송
        success, buffer = cv2.imencode(".jpg", annotated_frame)
        if not success:
            continue

        frame_id = f"pdf_p{page_num + 1:03d}"
        print(f"[{page_num + 1}/{total_pages}] AI 분석 및 전송 중... ({frame_id})")
        
        try:
            res = client.snapshot(buffer.tobytes(), frame_id=frame_id)
            # 에러가 나지 않도록 응답(res) 자체를 출력하도록 수정됨
            print(f" -> 성공! 서버 응답: {res}")
        except Exception as e:
            print(f" -> 전송 지연(Timeout) 또는 에러 발생: {e}")
            
        time.sleep(interval)

    doc.close()
    print("[*] 모든 AI 분석 및 전송이 완료되었습니다.")

if __name__ == "__main__":
    sample_pdf = "sample.pdf"  
    run_yolo_pipeline(sample_pdf, interval=2.0)