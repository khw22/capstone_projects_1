"""
apply_dashboard_patch.py - index.html / script.js 에 '지속 변화 후보' 패치를 자동 적용

  cd C:\\Users\\LG\\Desktop\\cctv-main
  python "test modul\\butt_miner\\apply_dashboard_patch.py" --dir server\\static_dashboard

- 적용 전에 index.html.bak, script.js.bak 으로 백업한다(이미 있으면 덮어쓰지 않음).
- 이미 적용된 곳은 건너뛴다(여러 번 실행해도 안전).
- 앵커를 하나라도 못 찾으면 두 파일 모두 수정하지 않고 멈춘다.
- 줄 끝(LF/CRLF)은 원본 그대로 유지한다.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

SECTION = """        <section id="butt-candidates" class="panel">
            <h2>지속 변화 후보 (꽁초·쓰레기) <span class="badge warn" id="butt-count">0건</span></h2>
            <p class="note">
                ※ 고정 CCTV 영상에서 오래 정지한 변화를 자동으로 모은 <b>후보</b>입니다. 쓰레기·꽁초로 자동 분류한 결과가 아니며
                사람 검수가 필요합니다. 실제 꽁초 검출 성능은 아직 검증되지 않았습니다.
            </p>
            <table class="data-table" id="butt-table">
                <thead>
                    <tr>
                        <th>시각</th>
                        <th>장치</th>
                        <th>상세</th>
                        <th>검수 패널</th>
                        <th>검수</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td class="muted" colspan="5">수신된 지속 변화 후보가 없습니다.</td></tr>
                </tbody>
            </table>
        </section>

"""
CALL = "if (window.renderButtCandidates) window.renderButtCandidates({arg});"


def split_nl(text):
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), nl


def patch_index(text):
    steps = []
    if "butt_candidates.js" not in text:
        a = '<script src="script.js"></script>'
        if a not in text:
            raise ValueError("index.html: script.js 태그를 찾지 못했습니다")
        text = text.replace(a, '<script src="butt_candidates.js"></script>\n    ' + a, 1)
        steps.append("index.html: butt_candidates.js 태그 추가")
    if 'href="#butt-candidates"' not in text:
        a = '<a href="#pollution">오염도 현황</a>'
        if a not in text:
            raise ValueError("index.html: '오염도 현황' 메뉴 링크를 찾지 못했습니다")
        text = text.replace(a, a + '\n                <a href="#butt-candidates">꽁초 후보</a>', 1)
        steps.append("index.html: 메뉴 링크 추가")
    if 'id="butt-candidates"' not in text:
        a = "<!-- 변경 전: 온습도 정보 섹션을 지우고 아래 내용으로 교체합니다 -->"
        if a not in text:
            raise ValueError("index.html: 섹션 삽입 위치(온습도 안내 주석)를 찾지 못했습니다")
        text = text.replace(a, SECTION + "        " + a, 1)
        steps.append("index.html: '지속 변화 후보' 섹션 추가")
    return text, steps


def patch_script(text):
    steps = []
    # (a) displayEvents 계산 직후
    if "renderButtCandidates(displayEvents)" not in text:
        pat = re.compile(r"^( *)(const displayEvents = events\.filter\([^\n]*\);)\n", re.M)
        m = pat.search(text)
        if not m:
            raise ValueError("script.js: displayEvents 계산 줄을 찾지 못했습니다")
        text = text[:m.end()] + m.group(1) + CALL.format(arg="displayEvents") + "\n" + text[m.end():]
        steps.append("script.js: loadEvents 에서 후보 표시 호출 추가")
    # (b) loadEvents 의 '장치 없음' 분기
    if "renderButtCandidates([])" not in text or text.count("renderButtCandidates([])") < 2:
        pat = re.compile(r"(async function loadEvents\(devices\) \{\n *if \(!devices\.length\) \{\n)( *)")
        m = pat.search(text)
        if not m:
            raise ValueError("script.js: loadEvents 의 '장치 없음' 분기를 찾지 못했습니다")
        if "renderButtCandidates([])" not in text[m.end():m.end() + 400].split("\n")[0]:
            text = text[:m.end(1)] + m.group(2) + CALL.format(arg="[]") + "\n" + text[m.end(1):]
            steps.append("script.js: 장치 없음 분기에 초기화 호출 추가")
    # (c) 화면 지우기 핸들러 (들여쓰기 16칸인 이벤트 표 비우기 줄)
    pat = re.compile(r'^( {16})(setBody\("events-table", fullWidthRow\("events-table", "이벤트가 없습니다\.", "muted"\)\);)\n', re.M)
    m = pat.search(text)
    if not m:
        raise ValueError("script.js: 화면 지우기 핸들러의 이벤트 표 비우기 줄을 찾지 못했습니다")
    nxt = text[m.end():m.end() + 120]
    if "renderButtCandidates" not in nxt:
        text = text[:m.end()] + m.group(1) + CALL.format(arg="[]") + "\n" + text[m.end():]
        steps.append("script.js: 화면 지우기에 초기화 호출 추가")
    return text, steps


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="server/static_dashboard")
    a = p.parse_args()
    d = Path(a.dir)
    files = {n: d / n for n in ("index.html", "script.js")}
    for n, f in files.items():
        if not f.is_file():
            sys.exit(f"{f} 가 없습니다. --dir 경로를 확인하세요.")
    if not (d / "butt_candidates.js").is_file():
        print("[경고] butt_candidates.js 가 이 폴더에 없습니다. 같은 폴더에 넣어야 화면에 표시됩니다.")

    out, all_steps = {}, []
    try:
        for n, fn in (("index.html", patch_index), ("script.js", patch_script)):
            raw = files[n].read_bytes().decode("utf-8")
            text, nl = split_nl(raw)
            new, steps = fn(text)
            out[n] = new.replace("\n", nl) if nl != "\n" else new
            all_steps += steps
    except ValueError as e:
        sys.exit(f"[중단] {e}\n두 파일 모두 수정하지 않았습니다. dashboard_patch.md 를 보고 수동으로 적용하세요.")

    if not all_steps:
        print("이미 모두 적용되어 있습니다. 변경 없음.")
        return
    for n, f in files.items():
        bak = f.with_name(f.name + ".bak")
        if not bak.exists():
            shutil.copy2(f, bak)
        f.write_bytes(out[n].encode("utf-8"))
    print("적용 완료:")
    for s in all_steps:
        print("  -", s)
    print("백업: index.html.bak, script.js.bak")


if __name__ == "__main__":
    main()
