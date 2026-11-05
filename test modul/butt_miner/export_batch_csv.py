import csv
import json
import pathlib

dev_batch_dir = pathlib.Path('runs/dev_batch')
folders = sorted([f for f in dev_batch_dir.glob('video_*') if f.is_dir()])
out_csv = dev_batch_dir / 'batch_report.csv'

with open(out_csv, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['영상폴더', '후보개수', '요약상태'])
    
    for folder in folders:
        cand_dir = folder / 'candidates'
        c_count = len([x for x in cand_dir.iterdir() if x.name.startswith('ID')]) if cand_dir.exists() else 0
        summary_exists = '있음' if (folder / 'pipeline_summary.json').exists() else '없음'
        writer.writerow([folder.name, c_count, summary_exists])

print(f"보고서용 CSV 저장 완료: {out_csv.resolve()}")