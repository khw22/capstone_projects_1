# 담배꽁초 후보 자동 추출 및 역학습 파이프라인

고정 CCTV 영상에서 담배꽁초 후보를 자동으로 모으고, 사람이 검수한 결과를 YOLOv8 재학습 데이터로 바꾸는 도구입니다.
캡스톤디자인 프로젝트 「CCTV 센서화 기반 환경 오염 및 화재 분석 시스템(환경지기)」의 일부입니다.

## 동작 방식

```
고정 CCTV 영상 → 배경 모델링(픽셀별 시간 중앙값) → 배경 차이·움직임 분석
→ 4단계 상태 머신 추적 → 후보 자동 저장(패널·JSON) → 사람 검수 → 학습셋 변환 → YOLOv8 재학습
```

| 단계 | 색 | 의미 |
|---|---|---|
| 1 | 노랑 | 정지 후보 (배경과 다르고 움직임 없음) |
| 2 | 파랑 | 동적 객체 (즉시 제외) |
| 3 | 보라 | 가려짐 (타이머 정지, 유예 후 재개 또는 폐기) |
| 4 | 빨강 | 지속 후보 (설정 시간 이상 정지, 최종 저장) |

오탐 억제: 사람 영역 제외(`--exclude-person`), 주변 밀도 필터(`--neighbor-ratio`), 수동 제외 영역(`--ignore-region`).

## 파일

| 파일 | 역할 |
|---|---|
| `butt_miner.py` | 후보 추출(`mine`)과 검수 결과의 학습셋 변환(`export`) |
| `make_synthetic.py` | 꽁초 합성 테스트 영상 생성, 정답 기록, 채점(`cutout`, `crop`, `make`, `evaluate`) |
| `size_sweep.py` | 꽁초 크기를 줄여 가며 검출 한계를 자동 측정 |
| `upload_candidates.py` | 후보를 중앙 서버(snapshots, events)로 전송 |
| `pull_reviews.py` | 대시보드에서 검수한 결과를 받아 `reviewed/` 폴더로 정리 |
| `apply_dashboard_patch.py` | 대시보드(`index.html`, `script.js`)에 후보 섹션 패치를 자동 적용 |
| `run_pipeline.py` | 영상 하나를 흔들림 측정 → 후보 추출 → 후보 요약 → (서버 업로드)까지 한 명령으로 실행 |
| `analyze_candidates.py`, `measure_shake.py`, `make_gt.py` | 후보 요약, 흔들림 측정, 직접 촬영 영상의 정답 파일 만들기 |
| `detect_litter.py` | 학습된 YOLO로 영상·스트림에서 자동 감지 |

## 설치

```
pip install -r requirements.txt
```

`--exclude-person`을 쓸 때만 `ultralytics`가 필요하고, 첫 실행 때 `yolov8n.pt`(COCO 사전학습)를 내려받습니다.

## 사용법

**1. 후보 추출** (영상은 카메라가 고정되어 있어야 합니다)

```
python butt_miner.py mine --video 영상.mp4 --out runs/결과 --debug-video runs/결과/debug.mp4 --exclude-person --diff-thr 20 --min-area 6 --persist-sec 10
```

결과는 `runs/결과/candidates/ID****/`에 `panel.jpg`(검수용 3x2 패널), `frame.jpg`, `frame.txt`(YOLO 라벨 초안), `meta.json`으로 저장됩니다.
`debug.mp4`를 보면서 노이즈가 반복되는 구역은 `--ignore-region x,y,w,h`로 제외합니다. 이 좌표는 카메라마다 다릅니다.

**2. 검수** : `panel.jpg`를 보고 후보 폴더를 `reviewed/good/`(진짜 꽁초) 또는 `reviewed/noise/`(오탐)로 옮깁니다.

**3. 학습셋 변환** : good은 전체 프레임과 bbox 라벨로, noise는 빈 라벨(Hard Negative)로 저장합니다.

```
python butt_miner.py export --reviewed reviewed --dataset datasets/butt --prefix 영상이름
```

**카메라별 설정 파일.** 같은 옵션을 반복해서 쓰지 않도록 JSON으로 저장해 불러올 수 있다(영상과 출력 경로는 제외). 명령줄 옵션이 설정 파일보다 우선하고, 알 수 없는 키는 오류로 알려 준다.

```
{"diff_thr": 20, "min_area": 6, "persist_sec": 10, "exclude_person": true, "ignore_region": ["0,0,720,115"]}
```

```
python butt_miner.py mine --video 영상.mp4 --out runs/결과 --config cam1.json
```

**카메라 흔들림 보정.** 바람 등으로 카메라가 흔들리면 질감이 있는 바닥 전체가 "움직임"으로 잡혀 후보가 사라질 수 있다. `measure_shake.py --video 영상.mp4`로 흔들림(px)을 재고, `mine --stabilize`로 첫 프레임 기준 평행 이동을 보정한다. 합성 영상(질감 배경, 프레임마다 ±1.5px 흔들림)에서 보정 없이는 후보가 0개였고 보정하면 2개 모두 검출됐다. 회전, 모션 블러, 큰 이동은 보정하지 못하며 실제 영상에서는 시험하지 못했다.

**라벨 박스 확대(선택).** 검출 박스가 꽁초보다 작게 나올 때만 `export --bbox-scale 1.5`처럼 중심 기준으로 확대한다. 기본값(1.0)은 그대로이며, 배경이 섞일 수 있어 결과를 직접 확인해야 한다.

**4. 재학습**

```
yolo train model=best.pt data=data.yaml epochs=50 imgsz=1280
```

## 합성 평가

실제 꽁초 영상이 없어도 검출 성능을 측정할 수 있도록, 고정 카메라 영상 위에 꽁초 사진을 합성하고 정답과 비교합니다.

```
python make_synthetic.py cutout --image 꽁초사진.jpg --out butt.png
python make_synthetic.py make --video 영상.mp4 --butt butt.png --out synth/synth.mp4 --auto 5 --seed 1 --region 0,650,1920,400 --auto-start 25 --auto-spread 60 --duration 25
python butt_miner.py mine --video synth/synth.mp4 --out runs/synth --exclude-person --diff-thr 20 --min-area 6 --persist-sec 10
python make_synthetic.py evaluate --gt synth/synth_gt.json --candidates runs/synth/candidates
python size_sweep.py --video 영상.mp4 --butt butt.png --gt synth/synth_gt.json --scales 1.0 0.6 0.4 --out sweep --exclude-person --diff-thr 20 --min-area 6 --persist-sec 10
```

## 한 명령으로 실행 (run_pipeline.py)

```
python run_pipeline.py --video 영상.mp4 --out runs/결과 --preset litter
python run_pipeline.py --video 영상.mp4 --out runs/결과 --preset butt --server http://localhost:8000 --device-id local-ai-server
```

- 프리셋은 시작점이다(`butt` 꽁초용, `litter` 쓰레기용). 영상에 맞춰 `--config`(JSON), `--set key=value`, `--ignore-region`으로 조정한다.
- `--stabilize auto`는 흔들림을 재서 95% 이동량이 0.7px 이상이면 보정을 켠다. 60초보다 짧은 영상은 배경 수집을 자동으로 줄인다.
- 사용한 설정은 `pipeline_config.json`, 요약(후보 수, 처리 시간, 실시간 배수)은 `pipeline_summary.json`에 저장되어 실험 기록이 된다.
- 프리셋의 사람 제외는 `ultralytics`가 필요하다. 없으면 `--set exclude_person=false`.
- 합성한 흔들림·정상·짧은 영상과 가짜 서버로 시험했고, 실제 영상과 실제 서버에서는 시험하지 못했다.

## 중앙 서버·대시보드 연동

기존 통신 규약(v0.1)의 `snapshots`, `events` 엔드포인트만 사용해 후보를 올리고, 대시보드에서 검수한 결과를 회수한다.
서버에는 검수 결과 저장용 `reviews` 엔드포인트(GET, PUT)와 테이블을 추가한다(`server/api/models.py`, `storage.py`, `main.py`).
대시보드의 후보 섹션에는 요약 카드(전체, 오늘 감지, 검수 대기, 꽁초, 쓰레기 등)와 종류·검수 상태 필터, 정렬(최신순, 큰 것 먼저), 50건 단위 "더 보기"가 있다(화면에서만 적용되며 서버 데이터는 바뀌지 않는다).
대시보드에는 `butt_candidates.js`를 `server/static_dashboard/`에 넣고 `apply_dashboard_patch.py`로 `index.html`, `script.js`를 패치한다.

```
python upload_candidates.py --candidates runs/결과/candidates --server http://localhost:8000 --device-id local-ai-server --video-name 영상.mp4
(대시보드의 '꽁초 후보'에서 꽁초 / 기타 쓰레기 / 쓰레기 아님 / 불확실 선택)
python pull_reviews.py --candidates runs/결과/candidates --reviewed reviewed
python butt_miner.py export --reviewed reviewed --dataset datasets/butt --prefix 영상이름
```

- 장치 ID는 서버에 등록된 값이어야 한다(`GET /api/devices`). 등록되지 않은 장치로 보낸 이벤트는 대시보드에 표시되지 않는다.
- 합성 꽁초 후보로 업로드 → 검수 → 회수 → 학습셋 변환까지 시험했으며, 실제 꽁초 영상으로는 검증하지 못했다.
- 검수 저장 API에는 인증이 없고, 대시보드의 '화면 지우기'는 이벤트와 검수 결과를 영구 삭제한다.

## 2클래스 학습셋 (꽁초 / 쓰레기)

클래스는 0 = `cigarette_butt`(꽁초), 1 = `litter`(꽁초를 제외한 쓰레기 전체)로 정의한다. 화재 감지는 별도 모델로 두는 것을 권장한다(꽁초는 수 px의 작은 물체라 imgsz 확대나 SAHI가 필요하지만 화재는 큰 객체이기 때문이며, 아직 비교 시험은 하지 않았다).

| 대시보드 라벨 | 폴더 | 학습셋 변환 |
|---|---|---|
| 꽁초 | `reviewed/good` | 클래스 0 + bbox |
| 쓰레기(꽁초 제외) | `reviewed/other_litter` | 클래스 1 + bbox |
| 쓰레기 아님 | `reviewed/noise` | 빈 라벨 (Hard Negative) |
| 불확실 | `reviewed/uncertain` | 변환 대상 아님 |

- 큰 쓰레기도 후보가 되도록 `--max-area`를 올린다(기본 800). 합성한 큰 물체(약 2,450px)로 `--max-area 5000`에서 잡히는 것을 확인했으며, 실제 값은 영상에 맞춰 정해야 한다.
- `export`는 쓰레기 폴더가 있으면 클래스 1로 저장하고, `data.yaml`이 없을 때만 새로 만든다(`--names`, `--litter-class`로 조정). 검증용 이미지는 `--split val`로 따로 내보낸다.
- 쓰레기 클래스의 성능은 아직 시험하지 않았다. 쓰레기와 놓아 둔 짐(가방, 자전거 등)은 자동으로 구분되지 않아 검수 라벨과 학습이 구분을 맡는다.

## 자동 감지 (학습된 YOLO 사용)

`detect_litter.py`는 2클래스(꽁초/쓰레기)로 학습한 YOLO 모델로 영상이나 스트림에서 자동 감지하고 서버에 알린다. 학습된 모델이 있어야 하며, 현재 꽁초 1클래스 모델은 쓰레기를 찾지 못한다.

```
python detect_litter.py --model best.pt --source 영상.mp4 --out runs/detect1 --imgsz 1280 --persist-sec 10 --exclude-person
python detect_litter.py --model best.pt --source rtsp://주소 --out runs/live --server http://localhost:8000 --device-id local-ai-server
```

- 동작: YOLO 검출 → (선택) 사람 영역 제외 → 위치 기반 추적 → 같은 자리에서 `--persist-sec` 이상 정지하고 검출 비율(`--min-ratio`)을 채우면 이벤트 1회. 움직이는 물체는 정지 시간이 초기화되어 제외된다.
- 작은 물체는 `--imgsz`를 키우거나 `--tile 640`(타일 추론)을 쓴다. 느려지므로 실행 끝에 표시되는 평균 처리 시간으로 실시간 여유를 확인한다.
- 이벤트 폴더(`out/candidates/ID****`)는 후보와 같은 형식이라 대시보드 검수 → `pull_reviews.py` → `export` → 재학습 흐름에 그대로 쓴다. 검수 라벨이 모델 예측 클래스보다 우선한다(오탐은 "쓰레기 아님"으로 두면 Hard Negative가 된다).
- 서버에는 `cigarette_butt_detected`, `litter_detected` 이벤트와 heartbeat를 보낸다(`psutil`이 없으면 CPU·메모리는 0으로 보낸다).
- 시험 범위: 가짜 검출기로 로직(정지 시 1회 알림, 이동 물체 제외, 클래스 깜빡임 다수결, 서버 실패 시 계속 동작)을 확인했다. 실제 YOLO 모델(꽁초 1클래스)로는 오류 없이 실행되고 프레임당 약 163~176ms(CPU)였지만 합성 영상에서는 검출이 거의 없었다(`--gt` 진단 옵션으로 정답 위치별 검출 비율을 볼 수 있다). 실제 스트림 재연결과 장시간 운영은 시험하지 못했다.

## 측정 결과 요약 (합성 꽁초, 설정 `--diff-thr 20 --min-area 6 --persist-sec 10`)

| 꽁초 너비 | 영상 1 (단순한 바닥) | 영상 2 (자갈·잡초 바닥) |
|---|---|---|
| 약 23px | - | 5/5 |
| 약 18px | - | 5/5 |
| 약 16px | - | 5/5 |
| 약 14px | 5/5 | 3/5 |
| 약 9px | 2/5 | 2/5 |
| 약 5px | 0/5 | - |

`-`는 미측정입니다. 합성 평가의 오탐은 0개였고, 꽁초가 없는 영상의 오탐은 제외 영역 적용 후 놀이터 영상 19개 → 0개, 1-1 영상 3개 → 0개였습니다.

## 한계

- 모든 검출 평가는 **합성 꽁초 기준**이며, 실제 꽁초 영상으로는 아직 검증하지 못했습니다. 배율마다 꽁초 5개, 영상 2개라 경향을 보는 수준입니다.
- 안정적으로 잡히는 크기는 약 16px 이상입니다(1080p, 5cm 꽁초 가정 시 바닥 폭 약 6m).
- 제외 영역과 임계값은 카메라마다 다시 정해야 합니다.
- 재학습 후 YOLO 성능 변화, 처리 속도와 메모리 사용량은 측정하지 않았습니다.
- 검출 박스가 꽁초 전체보다 작게 나오는 경우가 있어, 라벨 초안은 검수에서 박스 범위를 확인해야 합니다.

## 데이터 안내

이 저장소에는 영상, 데이터셋, 학습 가중치를 포함하지 않습니다. 평가에 쓴 영상은 외부 CCTV 데이터이며 이용 조건에 따라 재배포할 수 없습니다.
