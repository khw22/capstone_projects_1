"""
aihub_meta.py - AI-Hub 불법투기 영상의 '파일 이름'에서 조건 정보(장소·날씨·시간대·기기·각도)를 뽑아 표로 만들고, 장소 단위로 분할한다
영상을 열지 않아서 몇 초면 끝난다(zip 안의 이름만 읽음).

  python aihub_meta.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --out runs/meta
  python aihub_meta.py --dir ...zip --out runs/meta --val-sites ydsp --test-sites ggp,tjdp

파일 이름 예: C_3_17_smp_ra_09-10_12-34-00_b_aft_DF1.mp4  ->  _ 로 쪼갠 위치(0부터)
  2 시나리오 번호(폴더 3-17 과 같음)  3 장소 코드  4 날씨로 추정(su/cl/ra)  5 날짜(월-일)  6 시각
  7 각도(a/b/c)  8 시간대로 추정(aft/for/set/rise/nig)  9 기기 코드로 추정(DF1/DF4)
※ 4, 8, 9번째 조각의 의미는 추정이다. 실제 영상으로 확인하고 쓸 것(inventory_videos.py 의 contact_sheet.jpg 등).
같은 사건(시나리오+장소+날짜)의 여러 각도 영상은 항상 같은 분할에 넣는다(장소 단위로 나누므로 자동으로 지켜짐).
"""
import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import list_videos  # noqa: E402

SCHEME = {"scenario_no": 2, "site": 3, "weather": 4, "date": 5, "time": 6, "angle": 7, "tod": 8, "device": 9}


def parse(name):
    stem = Path(name.split("::")[-1]).stem
    t = stem.split("_")
    if len(t) < 10:
        return None
    d = {k: t[i] for k, i in SCHEME.items()}
    d["event_id"] = "_".join([t[2], t[3], t[5]])           # 시나리오+장소+날짜(각도별로 촬영 시각이 몇 초 다를 수 있어 시각은 뺌)
    m = re.search(r"3-\d+\s*([^/\\]*)", name.replace("\\", "/").split("/")[-2]) if "/" in name else None
    d["scenario"] = m.group(0).strip() if m else ""
    d["file"] = name
    return d


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--out", default="runs/meta")
    p.add_argument("--val-sites", default="", help="검증용 장소 코드(쉼표 구분)")
    p.add_argument("--test-sites", default="", help="시험용 장소 코드(쉼표 구분)")
    a = p.parse_args()
    if not Path(a.dir).exists():
        sys.exit(f"경로를 찾을 수 없습니다: {a.dir}  (예시 글자 '...zip'이 아니라 실제 zip 파일 경로를 쓰세요)")
    items = list_videos(a.dir)
    rows, bad = [], []
    for it in items:
        d = parse(it["name"])
        (rows if d else bad).append(d or it["name"])
    if not rows:
        sys.exit("이름 규칙에 맞는 영상이 없습니다(조각 10개).")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    events = defaultdict(list)
    for r in rows:
        events[r["event_id"]].append(r)
    for e, v in events.items():
        for r in v:
            r["n_angles"] = len(v)
    print(f"영상 {len(rows)}개 = 사건 {len(events)}개 (사건 = 시나리오+장소+날짜, 각도 여러 개)")
    print("사건당 영상 수:", dict(Counter(len(v) for v in events.values())), " (3이 아니면 각도가 빠졌거나 같은 사건이 아닐 수 있음)")
    if bad:
        print(f"[참고] 이름 규칙에 안 맞는 영상 {len(bad)}개 제외")

    def table(title, key):
        ev, vid = defaultdict(set), Counter()
        for r in rows:
            ev[r[key]].add(r["event_id"])
            vid[r[key]] += 1
        print(f"\n[{title}] 값: 영상 수 / 사건 수")
        print("  " + ", ".join(f"{k}: {vid[k]} / {len(ev[k])}" for k in sorted(vid, key=lambda x: -vid[x])))
    table("장소(3번째 조각)", "site")
    table("날씨로 추정(4번째)", "weather")
    table("시간대로 추정(8번째)", "tod")
    table("기기로 추정(9번째)", "device")
    table("각도(7번째)", "angle")
    # 장소와 다른 조건이 겹치는지(교란) 확인: 장소로 나누면 날씨·시간대·기기도 함께 갈릴 수 있다
    def cross(title, key):
        ev = defaultdict(set)
        for r in rows:
            ev[(r["site"], r[key])].add(r["event_id"])
        sites = sorted({r["site"] for r in rows})
        vals = sorted({r[key] for r in rows})
        print(f"\n[장소 x {title}] 사건 수")
        print("  " + f"{'장소':6}" + "".join(f"{v:>6}" for v in vals))
        for s_ in sites:
            print("  " + f"{s_:6}" + "".join(f"{len(ev.get((s_, v), ())):6}" for v in vals))
    cross("날씨(추정)", "weather")
    cross("시간대(추정)", "tod")
    cross("기기(추정)", "device")
    mon = Counter(r["date"].split("-")[0] for r in rows)
    print("\n[촬영 월] " + ", ".join(f"{k}월 {v}개" for k, v in sorted(mon.items())))
    few = [k for k, v in Counter(r["site"] for r in rows).items() if len({x['event_id'] for x in rows if x['site'] == k}) < 3]
    if few:
        print(f"[주의] 사건이 3개 미만인 장소: {few} -> 단독으로 시험 장소로 쓰면 결과가 불안정함")

    split = {}
    val = {s.strip() for s in a.val_sites.split(",") if s.strip()}
    test = {s.strip() for s in a.test_sites.split(",") if s.strip()}
    for r in rows:
        r["split"] = "test" if r["site"] in test else ("val" if r["site"] in val else "train")
    if val or test:
        print("\n[장소 단위 분할]")
        for sp in ("train", "val", "test"):
            sub = [r for r in rows if r["split"] == sp]
            print(f"  {sp}: 영상 {len(sub)}개, 사건 {len({r['event_id'] for r in sub})}개, 장소 {sorted({r['site'] for r in sub})}")
    cols = ["file", "event_id", "scenario_no", "scenario", "site", "weather", "date", "time", "angle", "tod", "device", "n_angles", "split"]
    with open(out / "meta.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=cols)
        wr.writeheader()
        wr.writerows(sorted(rows, key=lambda r: (r["event_id"], r["angle"])))
    print(f"\n저장: {out / 'meta.csv'}")


if __name__ == "__main__":
    main()
