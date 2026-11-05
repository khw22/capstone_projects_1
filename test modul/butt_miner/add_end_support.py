"""
add_end_support.py - 이미 만들어 둔 일괄 추출 결과(후보 폴더)에 '끝 장면 지지도(end_support)'를 영상을 다시 돌리지 않고 추가한다

  python add_end_support.py --dir C:\\Users\\LG\\Downloads\\trash_dumping_videos.zip --meta runs/meta/meta.csv --batch runs/batch_a50 --batch runs/batch_c1

- 영상마다 처음 장면과 끝 장면의 차이 마스크를 한 번 만들고, 각 batch 폴더의 후보마다 상자 안에서 차이가 있는 비율(0~1)을 계산한다.
- 결과는 후보 파일을 고치지 않고 batch/영상이름/end_support.json 에 따로 저장한다(기준선 폴더는 파일 하나만 추가됨).
- 그 뒤 gt_tools.py evaluate --sweep 0,0.1,0.2,0.3,0.5 로 기준별 검출률·정밀도를 한 번에 비교한다.
- 영상 추출(후보 만들기)에는 영상당 1~7분이 걸렸지만 이 단계는 영상당 수 초~십수 초다.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_tools  # noqa: E402
from video_source import list_videos, open_video, read_csv  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--batch", action="append", required=True, help="후보 폴더를 가진 batch 결과 폴더(여러 번 지정 가능)")
    p.add_argument("--tmp", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--diff-thr", type=int, default=30)
    a = p.parse_args()
    batches = [Path(b) for b in a.batch]
    rows = {gt_tools.key_of(r["file"]): r for r in read_csv(a.meta)}
    keys = sorted({d.name for b in batches for d in b.iterdir() if d.is_dir() and (d / "candidates").exists()})
    keys = [k for k in keys if k in rows]
    if a.limit:
        keys = keys[:a.limit]
    items = {it["name"]: it for it in list_videos(a.dir)}
    print(f"영상 {len(keys)}개 처리(batch 폴더 {len(batches)}개)")
    done = 0
    for i, k in enumerate(keys, 1):
        item = items.get(rows[k]["file"])
        if item is None:
            print(f"[{i}/{len(keys)}] zip 에 없음: {k}")
            continue
        with open_video(item, a.tmp) as vp:
            em = gt_tools.end_mask(vp, a.diff_thr)
        if em is None:
            print(f"[{i}/{len(keys)}] {k}: 프레임을 읽지 못함")
            continue
        mk = em["mask"]
        msg = []
        for b in batches:
            d = b / k
            if not (d / "candidates").exists():
                continue
            sup = {}
            for mp in sorted((d / "candidates").glob("ID*/meta.json")):
                try:
                    x, y, w, h = json.loads(mp.read_text(encoding="utf-8"))["bbox_xywh"]
                except Exception:
                    continue
                x, y = max(0, x), max(0, y)
                reg = mk[y:y + h, x:x + w]
                sup[mp.parent.name] = round(float((reg > 0).mean()) if reg.size else 0.0, 3)
            (d / "end_support.json").write_text(json.dumps(sup, ensure_ascii=False), encoding="utf-8")
            hi = sum(1 for v in sup.values() if v >= 0.3)
            msg.append(f"{b.name}: 후보 {len(sup)}개 중 0.3 이상 {hi}개")
        done += 1
        print(f"[{i}/{len(keys)}] {k}  " + " | ".join(msg))
    print(f"\n완료: {done}개 영상. 이제 gt_tools.py evaluate --gt runs/gt/gt_events.json --batch <폴더> --sweep 0,0.1,0.2,0.3,0.5")


if __name__ == "__main__":
    main()
