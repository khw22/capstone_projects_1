"""
cleanup_db.py - 쌓인 후보·자동 감지 이벤트를 '선택해서' 정리 (대시보드의 '화면 지우기'는 전체 삭제라서 따로 만든 도구)

  python cleanup_db.py --repo C:\\Users\\LG\\Desktop\\cctv-main --older-than-days 7               # 미리보기(기본)
  python cleanup_db.py --repo C:\\Users\\LG\\Desktop\\cctv-main --older-than-days 7 --apply        # 실제 삭제
  python cleanup_db.py --repo ... --labels not_litter,uncertain --apply                         # 검수 결과가 '쓰레기 아님/불확실'인 것만
  python cleanup_db.py --repo ... --only-reviewed --older-than-days 3 --apply                   # 검수 끝난 것 중 3일 지난 것만
  python cleanup_db.py --repo ... --contains "(테스트)" --apply                                  # send_test_event.py 로 만든 테스트 이벤트만

- 조건(--older-than-days, --labels, --only-reviewed, --contains)은 모두 만족해야 삭제되며, 하나도 안 주면 실행하지 않는다.
- 삭제 대상은 후보·자동 감지 이벤트(persistent_change_candidate, cigarette_butt_detected, litter_detected)뿐이다.
  화재 등 다른 이벤트와 장치 정보는 건드리지 않는다(--types 로 바꿀 수 있음).
- 이벤트와 함께 그 검수 결과, 다른 이벤트가 쓰지 않는 스냅샷 이미지(DB 용량의 대부분)도 지운다.
- 기본은 미리보기이고 --apply 를 줘야 삭제한다. 삭제 전에 DB 백업 파일을 자동으로 만든다. 서버를 끄고 실행하는 것을 권장한다.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TYPES = "persistent_change_candidate,cigarette_butt_detected,litter_detected"


def find_db(a):
    if a.db:
        return Path(a.db)
    if os.environ.get("CCTV_DB_PATH"):
        return Path(os.environ["CCTV_DB_PATH"])
    if a.repo:
        return Path(a.repo) / "data" / "cctv.db"
    return Path("data/cctv.db")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=None, help="저장소 루트(<repo>/data/cctv.db 를 사용)")
    p.add_argument("--db", default=None, help="DB 파일 경로(지정하면 --repo 보다 우선)")
    p.add_argument("--older-than-days", type=float, default=None)
    p.add_argument("--labels", default="", help="쉼표로 구분: cigarette_butt,other_litter,not_litter,uncertain")
    p.add_argument("--only-reviewed", action="store_true", help="검수가 끝난 항목만")
    p.add_argument("--contains", default="", help="메시지에 이 글자가 들어 있는 것만 (예: \"(테스트)\" -> send_test_event.py 로 만든 이벤트)")
    p.add_argument("--device-id", default="")
    p.add_argument("--types", default=DEFAULT_TYPES)
    p.add_argument("--apply", action="store_true", help="실제로 삭제(없으면 미리보기)")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--vacuum", action="store_true", help="삭제 후 DB 파일 크기를 줄임(시간이 걸릴 수 있음)")
    a = p.parse_args()

    labels = {x.strip() for x in a.labels.split(",") if x.strip()}
    if a.older_than_days is None and not labels and not a.only_reviewed and not a.contains:
        sys.exit("조건이 없습니다. --older-than-days, --labels, --only-reviewed, --contains 중 하나 이상을 주세요(전체 삭제 방지).")
    types = {x.strip() for x in a.types.split(",") if x.strip()}
    db = find_db(a)
    if not db.exists():
        sys.exit(f"DB 파일이 없습니다: {db.resolve()}  (--repo 나 --db 로 지정)")

    con = sqlite3.connect(str(db), timeout=30)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "events" not in tables:
        sys.exit("events 테이블이 없습니다. 올바른 DB 인지 확인하세요.")
    review = {}
    if "reviews" in tables:
        review = {(r["device_id"], r["candidate_id"]): r["label"] for r in con.execute("SELECT device_id, candidate_id, label FROM reviews")}
    cutoff = None
    if a.older_than_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=a.older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = con.execute("SELECT id, device_id, timestamp, payload_json FROM events").fetchall()
    victims, keep_refs = [], set()
    for r in rows:
        try:
            ev = json.loads(r["payload_json"])
        except Exception:
            keep_refs.add(None)
            continue
        cid = ev.get("related_detection_id")
        label = review.get((r["device_id"], cid))
        hit = ev.get("event_type") in types and (not a.device_id or r["device_id"] == a.device_id)
        if hit and cutoff is not None:
            hit = bool(r["timestamp"]) and r["timestamp"] < cutoff
        if hit and a.only_reviewed:
            hit = label is not None
        if hit and labels:
            hit = label in labels
        if hit and a.contains:
            hit = a.contains in (ev.get("message") or "")
        if hit:
            victims.append((r["id"], r["device_id"], cid, ev.get("snapshot_ref"), ev.get("event_type"), label))
        elif ev.get("snapshot_ref"):
            keep_refs.add(ev["snapshot_ref"])

    snap_ids = {v[3] for v in victims if v[3] and v[3] not in keep_refs}
    freed = 0
    if snap_ids and "snapshots" in tables:
        for sid in snap_ids:
            row = con.execute("SELECT length(data) FROM snapshots WHERE snapshot_id = ?", (sid,)).fetchone()
            freed += row[0] or 0

    print(f"DB: {db.resolve()}  (이벤트 {len(rows)}건 중 삭제 대상 {len(victims)}건)")
    cond = []
    if cutoff:
        cond.append(f"{a.older_than_days:g}일보다 오래됨(~{cutoff})")
    if a.only_reviewed:
        cond.append("검수 완료")
    if labels:
        cond.append(f"라벨 {sorted(labels)}")
    if a.contains:
        cond.append(f"메시지에 '{a.contains}' 포함")
    print("조건:", " AND ".join(cond), "| 대상 종류:", sorted(types))
    if victims:
        print("종류별:", dict(Counter(v[4] for v in victims)))
        print("검수 상태별:", dict(Counter(v[5] or "미검수" for v in victims)))
        print(f"함께 지워질 스냅샷 {len(snap_ids)}개, 약 {freed / 1e6:.1f}MB")
    if not a.apply:
        print("\n미리보기입니다. 실제로 지우려면 같은 명령에 --apply 를 붙이세요(자동 백업 후 삭제).")
        return
    if not victims:
        print("삭제할 항목이 없습니다.")
        return

    if not a.no_backup:
        bak = db.with_name(f"{db.name}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        dst = sqlite3.connect(str(bak))
        con.backup(dst)
        dst.close()
        print(f"백업: {bak}")
    with con:
        con.executemany("DELETE FROM events WHERE id = ?", [(v[0],) for v in victims])
        if "reviews" in tables:
            con.executemany("DELETE FROM reviews WHERE device_id = ? AND candidate_id = ?",
                            [(v[1], v[2]) for v in victims if v[2]])
        if "snapshots" in tables:
            con.executemany("DELETE FROM snapshots WHERE snapshot_id = ?", [(s,) for s in snap_ids])
    if a.vacuum:
        con.execute("VACUUM")
    left = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"삭제 완료: 이벤트 {len(victims)}건, 스냅샷 {len(snap_ids)}개. 남은 이벤트 {left}건.")
    con.close()


if __name__ == "__main__":
    main()
