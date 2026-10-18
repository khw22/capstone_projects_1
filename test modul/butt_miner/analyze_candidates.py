"""
analyze_candidates.py - 후보가 너무 많을 때 원인(어디서, 어떤 크기로, 언제) 요약하고 줄이는 방법을 제안

  python analyze_candidates.py --candidates runs/결과/candidates [--cell 160] [--min-hot 3]

- 후보가 몰린 구역을 찾아 --ignore-region 후보를 제안한다(그 구역에 진짜 물체가 없는지 직접 확인할 것).
- 크기 기준(min-area)을 올렸을 때 후보가 몇 개 남는지 보여 준다.
- 같은 자리에 여러 번 생긴 후보(중복)를 묶어 센다.
"""
import argparse
import json
from collections import Counter
from math import hypot
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--cell", type=int, default=160, help="구역 격자 한 칸 크기(px)")
    p.add_argument("--top-large", type=int, default=8, help="면적이 큰 후보 상위 N개를 보여 줌(패널을 먼저 확인)")
    p.add_argument("--min-hot", type=int, default=3, help="이 개수 이상 몰린 칸을 '몰린 구역'으로 봄")
    a = p.parse_args()

    rows = []
    for d in sorted(Path(a.candidates).glob("ID*/meta.json")):
        m = json.loads(d.read_text(encoding="utf-8"))
        x, y, w, h = m["bbox_xywh"]
        rows.append({"id": d.parent.name, "x": x, "y": y, "w": w, "h": h, "area": w * h,
                     "t": m.get("detected_at_sec", 0), "res": m.get("resolution", [0, 0])})
    if not rows:
        raise SystemExit("후보가 없습니다(ID*/meta.json).")
    n = len(rows)
    W, H = rows[0]["res"] if rows[0]["res"][0] else (max(r["x"] + r["w"] for r in rows), max(r["y"] + r["h"] for r in rows))
    print(f"후보 {n}개 (화면 {W}x{H})\n")

    # 1) 크기
    areas = sorted(r["area"] for r in rows)
    q = lambda f: areas[min(n - 1, int(f * n))]
    print(f"[크기] 면적(px²) 최소 {areas[0]}, 중앙값 {q(0.5)}, 90% {q(0.9)}, 최대 {areas[-1]}")
    print("  min-area 를 올리면 남는 후보 수:", " | ".join(f"{t}: {sum(1 for v in areas if v >= t)}개" for t in (10, 20, 40, 80, 160, 320)))

    print(f"\n[큰 후보 상위 {a.top_large}개] 노이즈보다 실제 물체일 가능성이 높으니 이 후보의 panel.jpg 부터 확인하세요:")
    for r in sorted(rows, key=lambda r: -r["area"])[:a.top_large]:
        print(f"  {r['id']}: {r['w']}x{r['h']}px (면적 {r['area']}) 위치 {r['x']},{r['y']} 시각 {r['t']:.0f}초")

    # 2) 위치(몰린 구역)
    cells = Counter(((r["x"] + r["w"] // 2) // a.cell, (r["y"] + r["h"] // 2) // a.cell) for r in rows)
    hot = {c for c, k in cells.items() if k >= a.min_hot}
    print(f"\n[위치] {a.cell}px 격자에서 상위 칸:")
    for (cx, cy), k in cells.most_common(5):
        print(f"  x {cx * a.cell}~{(cx + 1) * a.cell}, y {cy * a.cell}~{(cy + 1) * a.cell}: {k}개 ({k * 100 // n}%)")
    seen, groups = set(), []                      # 이웃한 몰린 칸을 하나의 구역으로 합친다
    for c in sorted(hot):
        if c in seen:
            continue
        stack, comp = [c], []
        while stack:
            u = stack.pop()
            if u in seen or u not in hot:
                continue
            seen.add(u)
            comp.append(u)
            stack += [(u[0] + dx, u[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        groups.append(comp)
    if groups:
        print("  제안(그 구역에 진짜 물체가 없을 때만 사용):")
        for comp in sorted(groups, key=lambda g: -sum(cells[c] for c in g)):
            x0 = min(c[0] for c in comp) * a.cell
            y0 = min(c[1] for c in comp) * a.cell
            x1 = (max(c[0] for c in comp) + 1) * a.cell
            y1 = (max(c[1] for c in comp) + 1) * a.cell
            cnt = sum(cells[c] for c in comp)
            print(f"    --ignore-region {x0},{y0},{min(W, x1) - x0},{min(H, y1) - y0}   (후보 {cnt}개, {cnt * 100 // n}%)")
    else:
        print("  특정 구역에 몰려 있지 않습니다(흩어진 노이즈이거나 실제 물체가 많은 경우).")

    # 3) 중복(같은 자리)
    cl = []
    for r in rows:
        c = (r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
        for g in cl:
            if hypot(c[0] - g[0], c[1] - g[1]) < 40:
                g[2] += 1
                break
        else:
            cl.append([c[0], c[1], 1])
    print(f"\n[중복] 서로 40px 안에 있는 후보를 묶으면 고유 위치 {len(cl)}곳 (후보 {n}개)")
    if len(cl) < 0.7 * n:
        print(f"  같은 자리에 평균 {n / len(cl):.1f}번씩 저장됐습니다. mine 에 --dedupe-px 40 을 쓰면 중복이 줄어듭니다.")

    # 4) 시간
    ts = Counter(int(r["t"] // 10) * 10 for r in rows)
    print("\n[시간] 10초 구간별 후보 수:", ", ".join(f"{k}~{k + 10}s: {v}" for k, v in sorted(ts.items())))
    print("\n다음: 흔들림이 있으면 --stabilize, 크기가 작은 노이즈가 많으면 --min-area 상향, 한 구역에 몰렸으면 --ignore-region,")
    print("사람 근처면 --exclude-person, 큰 물체의 조각이면 --neighbor-ratio 를 낮추세요(작게 쪼개진 후보 감소).")


if __name__ == "__main__":
    main()
