import pathlib

dev_batch_dir = pathlib.Path('runs/dev_batch')
folders = sorted([f for f in dev_batch_dir.glob('video_*') if f.is_dir()])

print(f"=== 대표 {len(folders)}개 영상 일괄 추출 결과 요약 ===\n")

total_candidates = 0

for f in folders:
    cand_dir = f / 'candidates'
    c_count = 0
    if cand_dir.exists():
        # candidates 폴더 내 ID로 시작하는 후보 개수 측정
        c_count = len([x for x in cand_dir.iterdir() if x.name.startswith('ID')])
    total_candidates += c_count
    print(f"- {f.name}: 후보 {c_count}개 감지")

print(f"\n[전체 통계 요약]")
print(f"1. 총 분석 영상: {len(folders)}개")
print(f"2. 총 추출 후보 수: {total_candidates}개")
print(f"3. 영상당 평균 후보 수: {total_candidates / max(len(folders), 1):.1f}개")