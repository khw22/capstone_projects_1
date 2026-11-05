"""
video_source.py - 폴더 안의 영상과 zip 안의 영상을 똑같이 다루는 도우미 (압축을 전부 풀 필요 없음)

zip 안의 영상은 '하나씩' 임시 폴더에 풀어서 쓰고 바로 지운다. 필요한 여유 공간은 가장 큰 영상 1개 정도다.
inventory_videos.py 같은 도구가 이 모듈을 쓴다.

  from video_source import list_videos, open_video
  for item in list_videos(r"D:\\aihub\\videos"):          # 폴더(하위 폴더, zip 포함) 또는 zip 파일 하나
      with open_video(item, tmp="E:\\\\tmp") as path:      # path 는 임시로 풀린 영상 파일
          ...
"""
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

EXT = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


def fix_name(info):
    """zip 안의 한글 파일 이름이 깨지는 경우(cp949 로 저장된 zip)를 바로잡는다."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def list_videos(root):
    """영상 목록을 [{name, kind, path, member}] 로 돌려준다. name 은 화면에 보여 줄 이름."""
    root = Path(root)
    items = []
    if root.is_file() and root.suffix.lower() == ".zip":
        zips, files = [root], []
        base = root.parent
    else:
        files = sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in EXT)
        zips = sorted(f for f in root.rglob("*.zip"))
        base = root
    for f in files:
        items.append({"name": str(f.relative_to(base)), "kind": "file", "path": f, "member": None})
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    nm = fix_name(info)
                    if not info.is_dir() and Path(nm).suffix.lower() in EXT:
                        items.append({"name": f"{z.name}::{nm}", "kind": "zip", "path": z, "member": info.filename,
                                      "size": info.file_size})
        except zipfile.BadZipFile:
            print(f"[건너뜀] zip 이 아니거나 손상됨(분할 압축이면 먼저 이어 붙여야 함): {z.name}")
    return sorted(items, key=lambda x: x["name"])


@contextmanager
def open_video(item, tmp=None):
    """item 의 영상을 로컬 파일 경로로 열어 준다. zip 멤버는 임시로 풀었다가 블록이 끝나면 지운다."""
    if item["kind"] == "file":
        yield str(item["path"])
        return
    if tmp:                                               # 임시 폴더가 없으면 만들고, 드라이브가 없으면 이유를 알려 준다
        try:
            os.makedirs(tmp, exist_ok=True)
        except OSError as e:
            raise SystemExit(f"임시 폴더를 만들 수 없습니다: {tmp} ({e})\n"
                             "존재하는 드라이브의 폴더를 지정하거나 --tmp 를 빼세요(기본 임시 폴더 사용. 영상 1개 분량만 필요합니다).")
    d = tempfile.mkdtemp(prefix="vsrc_", dir=tmp)
    try:
        with zipfile.ZipFile(item["path"]) as zf:
            dst = Path(d) / ("video" + Path(item["member"]).suffix.lower())
            with zf.open(item["member"]) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
        yield str(dst)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def free_gb(path="."):
    return shutil.disk_usage(path).free / 1e9


def read_csv(path):
    """CSV 를 dict 목록으로 읽는다. 엑셀·메모장이 한글을 ANSI(cp949)로 저장해도 읽도록 인코딩을 차례로 시도한다."""
    import csv
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return list(csv.DictReader(text.splitlines()))
