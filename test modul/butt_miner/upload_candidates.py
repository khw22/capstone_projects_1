"""
upload_candidates.py - butt_miner 후보를 중앙 서버(COMMUNICATION_PROTOCOL v0.1)로 전송

기존 엔드포인트만 사용하므로 서버 수정이 필요 없다.
  1) POST /api/devices/{id}/snapshots  : 검수 패널(panel.jpg) 업로드  -> snapshot_id
  2) POST /api/devices/{id}/events     : event_type=persistent_change_candidate, snapshot_ref=snapshot_id

사용 예:
  python upload_candidates.py --candidates runs/결과/candidates --server http://localhost:8000 --device-id local-ai-server --dry-run
  python upload_candidates.py --candidates runs/결과/candidates --server http://localhost:8000 --device-id local-ai-server \
      --video-name 1-1.mp4 --video-start 2026-10-09T10:00:00Z

  - 검수 후 진짜 꽁초만 올리려면 --candidates reviewed/good 처럼 폴더를 지정한다.
  - 이미 올린 후보는 candidates 폴더의 uploaded.json 에 기록되어 다시 올리지 않는다.
  - API 키는 --api-key 또는 환경변수 DEVICE_API_KEY (서버가 현재 키 검사를 꺼 둬도 헤더는 항상 보낸다).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

EVENT_TYPE = "persistent_change_candidate"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_message(name, meta, video_name, snap_id, scene_id=None):
    # 주의: 대시보드가 message 에 대문자 'X:' 가 있으면 화재 이벤트로 취급하므로 쓰지 않는다.
    x, y, w, h = meta["bbox_xywh"]
    parts = [
        f"후보 {name}",
        f"위치 {x},{y}",
        f"크기 {w}x{h}px",
        f"정지 {meta.get('static_sec', 0):.1f}초",
    ]
    if video_name:
        parts.append(f"영상 {video_name}")
    parts.append(f"snap={snap_id}")           # 대시보드가 썸네일을 찾는 용도
    if scene_id:
        parts.append(f"scene={scene_id}")     # 대시보드가 '장면 보기' 링크를 만드는 용도(make_scene.py 가 만든 scene.jpg)
    return " | ".join(parts)


def api_error(resp):
    try:
        body = resp.json()
        err = body.get("error", {})
        return f"[{err.get('code', resp.status_code)}] {err.get('message', resp.text[:200])}"
    except Exception:
        return f"[http_{resp.status_code}] {resp.text[:200]}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True, help="ID**** 폴더들이 있는 경로")
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--device-id", default="local-ai-server", help="서버에 등록된 장치 ID (GET /api/devices 로 확인)")
    p.add_argument("--api-key", default=os.environ.get("DEVICE_API_KEY", ""))
    p.add_argument("--video-name", default="", help="표시용 영상 이름")
    p.add_argument("--video-start", default="", help="영상 시작 실제 시각(UTC, 예: 2026-10-09T10:00:00Z). 없으면 전송 시각 사용")
    p.add_argument("--severity", default="info", choices=["info", "warning", "critical"])
    p.add_argument("--limit", type=int, default=0, help="최대 전송 개수 (0이면 전부)")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--dry-run", action="store_true", help="서버에 보내지 않고 내용만 출력")
    p.add_argument("--ids", default="", help="쉼표로 구분한 후보 폴더 이름만 올린다(예: ID1237,ID2000)")
    p.add_argument("--redo", action="store_true", help="--ids 로 고른 후보는 이미 올린 기록이 있어도 다시 올린다(서버에서 먼저 지워야 중복되지 않음)")
    a = p.parse_args()

    root = Path(a.candidates)
    folders = sorted(d for d in root.glob("ID*") if (d / "meta.json").is_file() and (d / "panel.jpg").is_file())
    only = {x.strip() for x in a.ids.split(",") if x.strip()}
    if only:
        folders = [d for d in folders if d.name in only]
    if not folders:
        raise SystemExit(f"후보 폴더가 없습니다(ID*/meta.json, panel.jpg 필요): {root.resolve()}")

    start = None
    if a.video_start:
        start = datetime.strptime(a.video_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    log_path = root / "uploaded.json"
    done = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}

    base = a.server.rstrip("/")
    headers = {"X-Device-Api-Key": a.api_key} if a.api_key else {}
    prefix = Path(a.video_name).stem if a.video_name else root.parent.name

    if not a.dry_run:   # 미등록 장치로 보낸 이벤트는 대시보드에 표시되지 않으므로 먼저 확인
        try:
            r = requests.get(f"{base}/api/devices", timeout=a.timeout)
            ids = [d["device_id"] for d in r.json().get("devices", [])] if r.ok else None
        except Exception as e:
            raise SystemExit(f"서버에 연결할 수 없습니다 ({base}): {e}")
        if ids is not None and a.device_id not in ids:
            raise SystemExit(f"서버에 등록되지 않은 장치입니다: {a.device_id}\n  등록된 장치: {ids}\n"
                             f"  --device-id 를 위 목록 중 하나로 지정하세요.")
    sent = failed = skipped = 0

    for d in folders:
        if a.limit and sent >= a.limit:
            break
        if d.name in done and not (a.redo and d.name in only):
            skipped += 1
            continue
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        ts = iso(start + timedelta(seconds=float(meta.get("detected_at_sec", 0)))) if start else iso(datetime.now(timezone.utc))
        frame_id = f"cand_{prefix}_{d.name}"

        if a.dry_run:
            msg = build_message(d.name, meta, a.video_name, "snap_DRYRUN", "snap_DRYRUN" if ((d / "scene.gif").is_file() or (d / "scene.jpg").is_file()) else None)
            print(json.dumps({"timestamp": ts, "event_type": EVENT_TYPE, "severity": a.severity,
                              "message": msg, "related_detection_id": frame_id}, ensure_ascii=False))
            sent += 1
            continue

        try:
            with open(d / "panel.jpg", "rb") as f:
                r = requests.post(
                    f"{base}/api/devices/{a.device_id}/snapshots", headers=headers, timeout=a.timeout,
                    files={"file": ("panel.jpg", f, "image/jpeg")},
                    data={"metadata": json.dumps({"timestamp": ts, "frame_id": frame_id}, ensure_ascii=False)})
            if not r.ok:
                raise RuntimeError("snapshot " + api_error(r))
            snap_id = r.json()["snapshot_id"]

            scene_id = None
            for sname, sctype in (("scene.gif", "image/gif"), ("scene.jpg", "image/jpeg")):   # 움직이는 사진 우선, 거절되면 정지 사진
                sp = d / sname
                if not sp.is_file():
                    continue
                try:
                    with open(sp, "rb") as f:
                        r2 = requests.post(
                            f"{base}/api/devices/{a.device_id}/snapshots", headers=headers, timeout=a.timeout,
                            files={"file": (sname, f, sctype)},
                            data={"metadata": json.dumps({"timestamp": ts, "frame_id": frame_id + "_scene"}, ensure_ascii=False)})
                    if r2.ok:
                        scene_id = r2.json()["snapshot_id"]
                        break
                    print(f"[주의] {d.name}: {sname} 업로드 실패 {api_error(r2)}")
                except Exception as e2:
                    print(f"[주의] {d.name}: {sname} 업로드 실패 {e2}")

            ev = {"timestamp": ts, "event_type": EVENT_TYPE, "severity": a.severity,
                  "message": build_message(d.name, meta, a.video_name, snap_id, scene_id),
                  "related_detection_id": frame_id, "snapshot_ref": snap_id}
            r = requests.post(f"{base}/api/devices/{a.device_id}/events", headers=headers, json=ev, timeout=a.timeout)
            if not r.ok:
                raise RuntimeError("event " + api_error(r))
        except Exception as e:                                   # 한 건 실패해도 나머지는 계속
            failed += 1
            print(f"[실패] {d.name}: {e}")
            continue

        done[d.name] = {"snapshot_id": snap_id, "scene_id": scene_id, "timestamp": ts, "frame_id": frame_id}
        log_path.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
        sent += 1
        print(f"[전송] {d.name} -> {snap_id}")

    label = "미리보기" if a.dry_run else "전송"
    print(f"\n{label} {sent}건, 이미 올림 {skipped}건, 실패 {failed}건")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
