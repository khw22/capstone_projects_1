"""
scan_archives.py - 압축 파일 안을 '풀지 않고' 구조만 훑어서 한 장의 표로 보여 준다 (몇 초면 끝남)

  python scan_archives.py --dir D:\\aihub
  python scan_archives.py --dir D:\\aihub\\영상_01.zip --show 5

각 zip 에 영상·라벨(json/xml)·이미지가 몇 개 들어 있는지, 풀면 용량이 얼마인지, 중첩 zip·분할 압축·7z/rar 같은
'이 도구가 못 읽는 것'이 있는지, 영상과 라벨 이름이 짝이 맞는지, 라벨 파일 샘플(앞부분)을 보여 준다.
zip 의 목록(중앙 디렉터리)만 읽기 때문에 디스크 공간이 필요 없다.
"""
import argparse
import csv
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_source import EXT as VIDEO_EXT, fix_name  # noqa: E402

LABEL_EXT = {".json", ".xml", ".txt", ".csv"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
UNSUPPORTED = re.compile(r"\.(7z|rar|z\d\d|zip\.\d+|zip\.part\d+|part\d+)$", re.I)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, help="폴더 또는 zip 파일")
    p.add_argument("--show", type=int, default=3, help="zip 마다 보여 줄 파일 이름 예시 개수")
    p.add_argument("--csv", default="", help="결과를 CSV 로 저장할 경로(선택)")
    p.add_argument("--no-sample", action="store_true", help="라벨 파일 샘플 출력을 생략")
    p.add_argument("--tree", action="store_true", help="영상이 들어 있는 폴더별 개수를 보여 줌(분류 폴더 구조 파악용)")
    p.add_argument("--tokens", action="store_true", help="파일 이름을 _ 로 쪼개 위치별로 어떤 값이 몇 개씩 있는지 보여 줌(이름 규칙 파악용)")
    a = p.parse_args()
    root = Path(a.dir)
    if root.is_file():
        zips, others = ([root] if root.suffix.lower() == ".zip" else []), []
    else:
        zips = sorted(f for f in root.rglob("*.zip"))
        others = sorted(f for f in root.rglob("*") if f.is_file() and UNSUPPORTED.search(f.name))
    if not zips and not others:
        sys.exit("zip 파일을 찾지 못했습니다.")

    rows, vstems, lstems, sample = [], set(), set(), None
    all_videos = []
    print(f"{'zip':34} {'압축MB':>7} {'풀면MB':>7} {'영상':>5} {'라벨':>5} {'이미지':>5} {'기타':>5}  비고")
    for z in zips:
        try:
            zf = zipfile.ZipFile(z)
        except zipfile.BadZipFile:
            print(f"{z.name[:34]:34} {'-':>7} {'-':>7} {'-':>5} {'-':>5} {'-':>5} {'-':>5}  손상됨 또는 분할 압축(이어 붙여야 함)")
            continue
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            cnt, names = Counter(), []
            unc = comp = 0
            nested = 0
            for i in infos:
                nm = fix_name(i)
                ext = Path(nm).suffix.lower()
                unc += i.file_size
                comp += i.compress_size
                if ext in VIDEO_EXT:
                    cnt["video"] += 1
                    vstems.add(Path(nm).stem)
                    names.append(nm)
                    all_videos.append(nm)
                elif ext in LABEL_EXT:
                    cnt["label"] += 1
                    lstems.add(Path(nm).stem)
                    if sample is None and ext in {".json", ".xml"}:
                        raw = zf.open(i).read(2500)
                        for enc in ("utf-8", "cp949"):
                            try:
                                sample = (f"{z.name}::{nm}", raw.decode(enc, errors="strict"))
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            sample = (f"{z.name}::{nm}", raw.decode("utf-8", errors="replace"))
                elif ext in IMG_EXT:
                    cnt["img"] += 1
                elif ext == ".zip":
                    nested += 1
                else:
                    cnt["other"] += 1
            note = []
            if nested:
                note.append(f"중첩 zip {nested}개(안쪽은 이 도구가 못 읽음)")
            tops = Counter(Path(fix_name(i)).parts[0] for i in infos if len(Path(fix_name(i)).parts) > 1)
            if tops:
                note.append("최상위 폴더 " + ", ".join(f"{k}({v})" for k, v in tops.most_common(2)))
            print(f"{z.name[:34]:34} {comp / 1e6:7.0f} {unc / 1e6:7.0f} {cnt['video']:5} {cnt['label']:5} {cnt['img']:5} {cnt['other']:5}  {'; '.join(note)}")
            for nm in names[:a.show]:
                print(f"{'':36}└ {nm}")
            rows.append({"zip": z.name, "compressed_mb": round(comp / 1e6), "uncompressed_mb": round(unc / 1e6), "videos": cnt["video"],
                         "labels": cnt["label"], "images": cnt["img"], "other": cnt["other"], "nested_zip": nested})
    for f in others:
        print(f"{f.name[:34]:34} {f.stat().st_size / 1e6:7.0f} {'-':>7} {'-':>5} {'-':>5} {'-':>5} {'-':>5}  이 도구가 못 읽는 형식(분할 압축/7z/rar): 먼저 이어 붙이거나 풀어야 함")

    tv = sum(r["videos"] for r in rows)
    tu = sum(r["uncompressed_mb"] for r in rows)
    print(f"\nzip {len(rows)}개, 영상 {tv}개, 라벨 {sum(r['labels'] for r in rows)}개, 전부 풀면 약 {tu / 1000:.1f}GB")
    if vstems and lstems:
        both = vstems & lstems
        print(f"영상 이름과 라벨 이름이 같은 짝: {len(both)}개 / 영상 {len(vstems)}개 (이름이 달라도 라벨이 있을 수 있으니 샘플을 확인)")
    elif vstems and not lstems:
        print("라벨 파일(json/xml)이 이 범위에는 없습니다. 라벨이 별도 zip 에 있으면 그 zip 도 같이 지정하세요.")
    if others or any(r["nested_zip"] for r in rows):
        print("[주의] 읽을 수 없는 형식이 있습니다. 위 표의 비고를 확인하세요.")
    if a.tree and all_videos:
        folders = Counter(str(Path(n).parent).replace("\\", "/") for n in all_videos)
        print("\n[영상이 있는 폴더별 개수]")
        for k, v in sorted(folders.items()):
            print(f"  {v:4}  {k}")
    if a.tokens and all_videos:
        stems = [Path(n).stem.split("_") for n in all_videos]
        width = Counter(len(t) for t in stems)
        print(f"\n[파일 이름 규칙] 이름을 '_'로 나눈 조각 수: {dict(width)}")
        for pos in range(max(len(t) for t in stems)):
            vals = Counter(t[pos] for t in stems if len(t) > pos)
            shown = ", ".join(f"{k}({v})" for k, v in vals.most_common(8))
            more = f" 외 {len(vals) - 8}종" if len(vals) > 8 else ""
            print(f"  {pos}번째: 값 {len(vals)}종  {shown}{more}")
    if sample and not a.no_sample:
        print(f"\n[라벨 파일 샘플] {sample[0]} (앞 2500바이트)\n{sample[1]}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as fp:
            wr = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)
        print(f"\nCSV 저장: {a.csv}")


if __name__ == "__main__":
    main()
