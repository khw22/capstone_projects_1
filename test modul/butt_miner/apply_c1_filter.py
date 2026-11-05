from collections import defaultdict
import json
import pathlib
import re
import shutil


def parse_bbox_and_area(info):
  """bbox_xywh, area_px 등 실제 저장된 키 이름을 정확히 파싱합니다."""
  x, y, w, h, area = 0.0, 0.0, 0.0, 0.0, 0.0
  for k in ['bbox_xywh', 'bbox', 'box', 'rect', 'region']:
    if k in info:
      val = info[k]
      if isinstance(val, (list, tuple)) and len(val) >= 4:
        x, y, w, h = float(val[0]), float(val[1]), float(val[2]), float(val[3])
      elif isinstance(val, str):
        cleaned = (
            val.replace('(', '').replace(')', '').replace('[', '').replace(']', '')
        )
        parts = [float(p.strip()) for p in cleaned.split(',') if p.strip()]
        if len(parts) >= 4:
          x, y, w, h = parts[0], parts[1], parts[2], parts[3]
      elif isinstance(val, dict):
        x = float(val.get('x', val.get('left', 0)))
        y = float(val.get('y', val.get('top', 0)))
        w = float(val.get('w', val.get('width', 0)))
        h = float(val.get('h', val.get('height', 0)))
      break

  if w > 0 and h > 0:
    area = w * h
  else:
    for ak in ['area_px', 'area', 'pixel_area', 'size']:
      if ak in info:
        try:
          area = float(info[ak])
          break
        except Exception:
          pass
  return x, y, w, h, area


def get_id_keys(cand_folder_name, info):
  """ID 매칭용 키 집합을 생성합니다."""
  keys = set()
  keys.add(cand_folder_name)
  digits = re.findall(r'\d+', cand_folder_name)
  if digits:
    val = int(digits[0])
    keys.add(val)
    keys.add(str(val))
    keys.add(f'ID{val}')
    keys.add(f'ID{val:04d}')
  if isinstance(info, dict) and 'id' in info:
    cid = info['id']
    keys.add(cid)
    keys.add(str(cid))
    if isinstance(cid, int):
      keys.add(f'ID{cid}')
      keys.add(f'ID{cid:04d}')
  return keys


def process_c1_filtering(
    src_batch_dir,
    dst_batch_dir,
    min_area=120,  # 120px² 미만 미세 수풀/조명 노이즈 제거
    max_area=35000,
    max_cands_per_grid=3,  # 120px 격자 내 4회 이상 발생 시 고정 오탐 구역 차단
    grid_size=120,
):
  src_dir = pathlib.Path(src_batch_dir)
  dst_dir = pathlib.Path(dst_batch_dir)

  # [원인 해결] 이전 실행 잔여 폴더(2,850개 후보)를 완전 삭제 후 깨끗이 생성
  if dst_dir.exists():
    shutil.rmtree(dst_dir)
  dst_dir.mkdir(parents=True, exist_ok=True)

  video_folders = [
      f for f in src_dir.iterdir() if f.is_dir() and not f.name.startswith('.')
  ]
  print(
      '=== C1 노이즈 정제 및 결과 폴더 완전 초기화 진행 (총'
      f' {len(video_folders)}개 영상) ==='
  )

  total_before = 0
  total_after = 0

  for v_folder in video_folders:
    cand_dir = v_folder / 'candidates'
    target_v_dir = dst_dir / v_folder.name
    target_cand_dir = target_v_dir / 'candidates'
    target_cand_dir.mkdir(parents=True, exist_ok=True)

    if (v_folder / 'pipeline_config.json').exists():
      shutil.copy(
          v_folder / 'pipeline_config.json', target_v_dir / 'pipeline_config.json'
      )
    if (v_folder / 'run.log').exists():
      shutil.copy(v_folder / 'run.log', target_v_dir / 'run.log')

    if not cand_dir.exists():
      continue

    cands = [
        c for c in cand_dir.iterdir() if c.is_dir() and c.name.startswith('ID')
    ]
    total_before += len(cands)

    parsed_cands = []
    grid_counts = defaultdict(int)

    for c in cands:
      json_files = list(c.glob('*.json'))
      info = {}
      if json_files:
        try:
          with open(json_files[0], 'r', encoding='utf-8') as f:
            info = json.load(f)
        except Exception:
          pass

      x, y, w, h, area = parse_bbox_and_area(info)

      grid_x = int(x // grid_size)
      grid_y = int(y // grid_size)
      grid_key = (grid_x, grid_y)

      if area >= min_area:
        grid_counts[grid_key] += 1

      parsed_cands.append((c, area, grid_key, info))

    passed_keys = set()
    passed_count = 0

    for c, area, grid_key, info in parsed_cands:
      # Rule 1. 미세 노이즈 면적 필터링
      if area < min_area or area > max_area:
        continue

      # Rule 2. 공간 격자 고정 오탐 ROI 차단
      if grid_counts[grid_key] > max_cands_per_grid:
        continue

      dst_cand_item = target_cand_dir / c.name
      if dst_cand_item.exists():
        shutil.rmtree(dst_cand_item)
      shutil.copytree(c, dst_cand_item)
      passed_count += 1

      passed_keys.update(get_id_keys(c.name, info))

    total_after += passed_count

    # pipeline_summary.json 내부 candidates 리스트 항목 갱신
    orig_summary_file = v_folder / 'pipeline_summary.json'
    if orig_summary_file.exists():
      try:
        with open(orig_summary_file, 'r', encoding='utf-8') as f:
          summary_data = json.load(f)

        if 'candidates' in summary_data and isinstance(
            summary_data['candidates'], list
        ):
          filtered_candidates_list = []
          for cand_item in summary_data['candidates']:
            keep = False
            if isinstance(cand_item, dict):
              c_id = cand_item.get('id')
              c_name = cand_item.get('name', cand_item.get('folder', ''))
              if (
                  c_id in passed_keys
                  or str(c_id) in passed_keys
                  or c_name in passed_keys
              ):
                keep = True
              elif isinstance(c_id, int) and (
                  f'ID{c_id}' in passed_keys or f'ID{c_id:04d}' in passed_keys
              ):
                keep = True
            elif (
                isinstance(cand_item, (int, str)) and cand_item in passed_keys
            ):
              keep = True

            if keep:
              filtered_candidates_list.append(cand_item)

          summary_data['candidates'] = filtered_candidates_list
          summary_data['total_candidates'] = len(filtered_candidates_list)
          summary_data['candidate_count'] = len(filtered_candidates_list)
        else:
          summary_data['total_candidates'] = passed_count
          summary_data['candidate_count'] = passed_count

        with open(
            target_v_dir / 'pipeline_summary.json', 'w', encoding='utf-8'
        ) as f:
          json.dump(summary_data, f, ensure_ascii=False, indent=2)

      except Exception as e:
        print(f'[{v_folder.name}] summary 업데이트 경고: {e}')

    print(
        f'[{v_folder.name}] 정제 전: {len(cands):3d}개 -> 정제 후:'
        f' {passed_count:3d}개'
    )

  print(f'\n[C1 노이즈 정제 최종 요약]')
  print(f'1. 정제 전 총 후보 수: {total_before}개')
  print(
      f'2. 정제 후 총 후보 수: {total_after}개 (노이즈 {total_before - total_after}개'
      ' 제거)'
  )
  print(
      '3. 후보 감소율:'
      f' {((total_before - total_after) / max(total_before, 1)) * 100:.1f}% 필터링'
  )


if __name__ == '__main__':
  process_c1_filtering(
      'runs/batch_a50',
      'runs/batch_c1',
      min_area=120,
      max_cands_per_grid=3,
      grid_size=120,
  )