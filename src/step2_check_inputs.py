"""
STEP 2. 입력 데이터 사전 점검 (원본을 data/ 에 복사한 직후 실행)

엑셀(video_csv_info.xlsx) · 센서 CSV(raw/) · 영상 라벨(labels/)을 파이프라인과 같은 매칭 규칙으로 대조해
"조용히 빠지는 파일"을 미리 찾는다. 아무 파일도 수정하지 않음.

출력
    data/interim/input_check.csv   (issue, name, group, subject, exercise, detail)

실행 (preprocessing_1st/ 에서)
    python src/step2_check_inputs.py
"""
import os
import pandas as pd

from config import *
from common import load_meta, iter_raw_csvs, normalize_filename


def run():
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    meta = load_meta()
    rows = []

    def add(issue, name, detail='', m=None):
        rows.append({
            'issue': issue, 'name': name,
            'group': None if m is None else str(m.get('bal/unbal', '')).strip(),
            'subject': None if m is None else m.get('subject_id'),
            'exercise': None if m is None else m.get('exercise'),
            'detail': detail,
        })

    # ── 1. 센서 CSV ↔ 엑셀 ──
    found = {}
    for group in GROUPS:
        for exercise, path, key in iter_raw_csvs(group):
            fname = os.path.basename(path)
            if key in found:
                add('sensor_duplicate', fname, f"{found[key][1]} 와 같은 파일명")
            found[key] = (group, path)
            if key not in meta.index:
                add('sensor_not_in_meta', fname, f"raw/{os.path.relpath(path, RAW_DIR)} → 엑셀 csv_filename에 없음 (처리 안 됨)")
                continue
            m = meta.loc[key]
            if normalize_filename(fname) != fname.strip().lower():
                add('sensor_name_repaired', fname, f"'{key}'로 복원해 매칭함", m)
            meta_group = 'balance' if str(m['bal/unbal']).strip() == 'bal' else 'unbalance'
            if meta_group != group:
                add('group_mismatch', fname, f"폴더={group}, 엑셀={m['bal/unbal']}", m)

    for key, m in meta.iterrows():
        if key == 'nan' or key in found:
            continue
        normal = str(m.get('normal_data', '')).strip().lower()
        issue = 'meta_without_sensor(normal_data=x)' if normal == 'x' else 'meta_without_sensor'
        add(issue, key, f"normal_data={m.get('normal_data')}, 비고={m.get('_csv파일비고사항')}", m)

    # ── 2. 라벨 ↔ 엑셀 ──
    video_to_key = {v: k for k, v in meta['video_name_base'].items()}
    labeled = set()
    for fname in sorted(os.listdir(LABEL_DIR)):
        if fname.startswith('.'):
            continue
        if not fname.endswith('.csv'):
            add('label_not_csv', fname, "CSV가 아니라 읽지 않음 (CSV로 변환 필요)")
            continue
        video = fname[:-4]
        if video not in video_to_key:
            add('label_not_in_meta', fname, "엑셀 video_name과 매칭 안 됨 (파일명의 '(1)', 공백 등 확인)")
            continue
        labeled.add(video_to_key[video])
        try:
            cols = pd.read_csv(LABEL_DIR / fname, nrows=1).columns
            missing = [c for c in ['Start', 'Peak', 'End'] if c not in cols]
            if missing:
                add('label_missing_columns', fname, f"없는 컬럼: {missing}")
        except Exception as e:
            add('label_read_error', fname, str(e))

    for key in found:
        if key in meta.index and key not in labeled:
            add('sensor_without_label', key, f"영상={meta.loc[key, 'video_name']} (정렬 단계에서 제외됨)", meta.loc[key])

    report = pd.DataFrame(rows)
    report.to_csv(INTERIM_DIR / "input_check.csv", index=False, encoding='utf-8-sig')

    print(f"센서 CSV {len(found)}개 / 엑셀 {len(meta)}행 / 라벨 {len(labeled)}개 매칭")
    if report.empty:
        print("[OK] 문제 없음")
    else:
        print(report['issue'].value_counts().to_string())
        print(f"[INFO] 상세 → {INTERIM_DIR / 'input_check.csv'}")


if __name__ == "__main__":
    run()
