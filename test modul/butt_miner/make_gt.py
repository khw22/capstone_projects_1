"""
make_gt.py - 사람이 직접 잰 정답(위치, 시간)으로 채점용 정답 파일을 만든다

  python make_gt.py --out gt_litter.json --item 770,705,160,110,40,120

--item x,y,너비,높이,시작초,끝초  (여러 번 지정 가능, 좌표는 원본 영상 해상도 기준)
  - 위치는 프레임을 보고 직접 잰 값을 쓴다. 검출기가 낸 박스를 그대로 정답으로 쓰면 채점이 순환된다.
  - 시작초는 물체가 놓인 시점 이전, 끝초는 영상 끝(또는 그 이후)으로 넉넉히 잡는다.
    (채점은 '시작초 <= 후보 저장 시각 <= 끝초+2' 이고 위치가 가까운 후보를 검출로 센다.)

채점:
  python make_synthetic.py evaluate --gt gt_litter.json --candidates runs/결과/candidates
"""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--item", action="append", required=True, metavar="x,y,w,h,start,end")
    p.add_argument("--video", default="")
    a = p.parse_args()
    butts = []
    for i, s in enumerate(a.item, 1):
        v = [float(t) for t in s.split(",")]
        if len(v) != 6:
            raise SystemExit(f"--item 은 x,y,w,h,시작초,끝초 6개 값이 필요합니다: {s}")
        x, y, w, h, st, en = v
        if w <= 0 or h <= 0 or en <= st:
            raise SystemExit(f"값이 올바르지 않습니다(너비·높이 > 0, 끝초 > 시작초): {s}")
        butts.append({"id": i, "bbox_xywh": [int(x), int(y), int(w), int(h)], "start_sec": st, "end_sec": en})
    Path(a.out).write_text(json.dumps({"video": a.video, "manual": True, "butts": butts}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"저장: {a.out} (정답 {len(butts)}개)")


if __name__ == "__main__":
    main()
