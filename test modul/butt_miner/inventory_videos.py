"""
inventory_videos.py - 가지고 있는 영상들을 한 번에 훑어서 '무엇에 쓸 수 있는지' 판단할 정보를 표로 만든다

  python inventory_videos.py --dir C:\\Users\\LG\\Desktop\\videos --out runs/inventory
  python inventory_videos.py --dir D:\\aihub\\영상.zip --tmp E:\\tmp --out runs/inventory      # zip 을 풀지 않고 하나씩 임시로 풀어서 조사

출력: 영상별 해상도·fps·길이·카메라 흔들림, 비슷한 장면끼리 묶은 '장면 그룹', 첫 장면 모음 이미지(contact_sheet.jpg), inventory.csv
- 장면 그룹은 첫 프레임이 비슷한지로 추정한 값이다(같은 장소·같은 카메라인지 대략 확인용, 정확하지 않을 수 있음).
- 흔들림은 앞부분 일부 프레임으로 잰 값이다.
"""
import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_shake  # noqa: E402
from video_source import list_videos, open_video, free_gb  # noqa: E402


def thumb(frame):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    t = cv2.resize(g, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
    t -= t.mean()
    n = np.linalg.norm(t)
    return t / n if n > 1e-6 else t


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="영상이 들어 있는 폴더(하위 폴더, zip 포함) 또는 zip 파일")
    p.add_argument("--tmp", default=None, help="zip 안의 영상을 임시로 풀 폴더(C: 에 공간이 부족하면 다른 드라이브로 지정)")
    p.add_argument("--out", default="runs/inventory")
    p.add_argument("--same-scene", type=float, default=0.85, help="첫 프레임 상관이 이 값 이상이면 같은 장면으로 묶음")
    p.add_argument("--no-shake", action="store_true", help="흔들림 측정을 생략(빠름)")
    p.add_argument("--split", default="", help="학습/검증/시험 비율(예: 60,20,20). 같은 장면 그룹은 한쪽에만 들어가도록 나눠 split.csv 를 만든다")
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args()

    items = list_videos(a.dir)
    if not items:
        sys.exit(f"영상을 찾지 못했습니다: {a.dir}")
    zips = sum(1 for it in items if it["kind"] == "zip")
    if zips:
        biggest = max(it.get("size", 0) for it in items if it["kind"] == "zip")
        print(f"zip 안의 영상 {zips}개를 하나씩 임시로 풀어서 조사합니다(필요 여유 공간: 가장 큰 영상 약 {biggest / 1e6:.0f}MB, "
              f"임시 폴더 드라이브의 여유 {free_gb(a.tmp or '.'):.1f}GB)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, thumbs, firsts = [], [], []
    for it in items:
        with open_video(it, a.tmp) as vpath:
            cap = cv2.VideoCapture(vpath)
            if not cap.isOpened():
                print(f"[건너뜀] 열 수 없음: {it['name']}")
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            ok, f0 = cap.read()
            cap.release()
            if not ok:
                print(f"[건너뜀] 첫 프레임을 읽을 수 없음: {it['name']}")
                continue
            shake = ""
            if not a.no_shake:
                try:
                    m = measure_shake.measure(vpath, every=5, max_frames=300)
                    shake = round(float(np.percentile(m["mags"], 95)), 2) if m["mags"] else ""
                except SystemExit:
                    shake = ""
        rows.append({"file": it["name"], "width": w, "height": h, "fps": round(fps, 2),
                     "sec": round(n / fps, 1) if fps else 0, "shake_p95_px": shake})
        thumbs.append(thumb(f0))
        firsts.append(f0)

    # 장면 그룹(첫 프레임 유사도, union-find)
    k = len(rows)
    parent = list(range(k))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in range(k):
        for j in range(i + 1, k):
            if float(np.dot(thumbs[i], thumbs[j])) >= a.same_scene:
                parent[find(i)] = find(j)
    ids = {}
    for i in range(k):
        rows[i]["scene"] = ids.setdefault(find(i), len(ids) + 1)

    print(f"{'#':>2} {'장면':>4} {'해상도':>10} {'fps':>5} {'길이(초)':>8} {'흔들림p95':>9}  파일")
    for i, r in enumerate(rows, 1):
        print(f"{i:>2} {r['scene']:>4} {str(r['width']) + 'x' + str(r['height']):>10} {r['fps']:>5} {r['sec']:>8} {str(r['shake_p95_px']):>9}  {r['file']}")
    total = sum(r["sec"] for r in rows)
    print(f"\n영상 {k}개, 총 {total / 60:.1f}분, 서로 다른 장면(추정) {len(ids)}개")
    if a.split:                                           # 장면(장소) 단위 분할: 같은 장소의 영상이 학습과 시험에 섞이면 성능이 부풀려진다
        import random
        ratio = [float(x) for x in a.split.split(",")]
        names = ["train", "val", "test"][:len(ratio)]
        target = [r / sum(ratio) * k for r in ratio]
        groups = {}
        for r in rows:
            groups.setdefault(r["scene"], []).append(r)
        order = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        rnd = random.Random(a.seed)
        rnd.shuffle(order)
        order.sort(key=lambda kv: -len(kv[1]))
        count = [0] * len(names)
        for scene, members in order:
            j = max(range(len(names)), key=lambda i: (target[i] - count[i]) / max(target[i], 1e-9))
            for m in members:
                m["split"] = names[j]
            count[j] += len(members)
        print("분할(장면 단위):", ", ".join(f"{n} {sum(1 for r in rows if r['split'] == n)}개" for n in names))
        for n in names:
            sc = sorted({r["scene"] for r in rows if r["split"] == n})
            print(f"  {n}: 장면 {sc}")
        print("[주의] 장면 그룹은 첫 프레임 유사도로 추정한 값입니다. contact_sheet.jpg 로 같은 장소가 다른 그룹으로 갈라지지 않았는지 확인하고, 필요하면 split.csv 를 직접 고치세요.")
    low = [r["file"] for r in rows if r["fps"] and r["fps"] < 5]
    short = [r["file"] for r in rows if r["sec"] < 60]
    shaky = [r["file"] for r in rows if isinstance(r["shake_p95_px"], float) and r["shake_p95_px"] >= 3]
    if low:
        print(f"[참고] fps 5 미만 {len(low)}개: 정지 판정이 프레임 간격(0.2초 이상)에 영향을 받음")
    if short:
        print(f"[참고] 60초 미만 {len(short)}개: 배경 수집과 정지 판정에 시간이 부족할 수 있음")
    if shaky:
        print(f"[참고] 흔들림 95% 3px 이상 {len(shaky)}개: 흔들림 보정이 필요할 수 있음")

    with open(out / "inventory.csv", "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    if a.split:
        with open(out / "split.csv", "w", newline="", encoding="utf-8-sig") as fp:
            wr = csv.writer(fp)
            wr.writerow(["file", "scene", "split"])
            for r in rows:
                wr.writerow([r["file"], r["scene"], r["split"]])
    # 첫 장면 모음 이미지
    cols = 4
    cells = []
    for i, (f, r) in enumerate(zip(firsts, rows), 1):
        c = cv2.resize(f, (320, 180))
        cv2.putText(c, f"#{i} scene {r['scene']}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        cells.append(c)
    while len(cells) % cols:
        cells.append(np.zeros((180, 320, 3), np.uint8))
    sheet = np.vstack([np.hstack(cells[i:i + cols]) for i in range(0, len(cells), cols)])
    cv2.imwrite(str(out / "contact_sheet.jpg"), sheet)
    print(f"저장: {out / 'inventory.csv'}, {out / 'contact_sheet.jpg'}")


if __name__ == "__main__":
    main()
