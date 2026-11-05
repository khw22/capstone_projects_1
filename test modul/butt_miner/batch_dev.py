import pathlib
import subprocess
from video_source import list_videos, open_video

zip_path = r'C:\Users\LG\Downloads\trash_dumping_videos.zip'
out_dir = pathlib.Path('runs/dev_batch')
out_dir.mkdir(parents=True, exist_ok=True)
dev_sites = {'smp', 'erip'}
count = 0

# zip 내 영상 목록 가져오기
videos = list_videos(zip_path)

for v in videos:
    vname = pathlib.Path(v['member']).name
    parts = vname.split('_')
    if len(parts) >= 4 and parts[3] in dev_sites:
        count += 1
        video_out = out_dir / f'video_{count:03d}_{parts[3]}'
        print(f'[{count}] 처리 중: {vname}')
        
        # open_video 인자 tmp='tmp' 적용
        with open_video(v, tmp='tmp') as tmp_file:
            cmd = ['python', 'run_pipeline.py', '--video', str(tmp_file), '--out', str(video_out), '--preset', 'both']
            subprocess.run(cmd)