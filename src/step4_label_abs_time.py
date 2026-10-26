"""
STEP 4. 영상 라벨(상대시간) → 임시 절대시간

- labels/<video_name>.csv 의 Start/Peak/End ('mm:ss.sss', 영상 상대시간)를 초로 변환
- video_name으로 video_csv_info.xlsx와 매칭 → start_time + 상대초 = 임시 절대시간
- 센서와 같은 기준(start_time)이므로 step5에서 offset 하나만 구하면 정렬됨

출력
    data/interim/absolute_labels.csv   (filename, Index, Start, Peak, End)

실행 (preprocessing_1st/ 에서)
    python src/step4_label_abs_time.py
"""
import os
import numpy as np
import pandas as pd
from tqdm import tqdm

from config import *
from common import load_meta, time_str_to_sec

EXPECTED_COLUMNS = ['filename', 'Index', 'Start', 'Peak', 'End']


def run():
    meta_df = load_meta().reset_index()
    all_label_dfs, skipped = [], []

    for fname in tqdm(sorted(os.listdir(LABEL_DIR))):
        if not fname.endswith(".csv"):
            continue
        video_name = fname.replace(".csv", "")

        meta_row = meta_df[meta_df['video_name_base'] == video_name]
        if meta_row.empty:
            skipped.append(video_name)
            continue
        meta_row = meta_row.iloc[0]

        df_label = pd.read_csv(LABEL_DIR / fname, dtype={'Start': str, 'Peak': str, 'End': str})

        # ── 상대시간 → 초 → 절대시간 ──
        for col in ['Start', 'Peak', 'End']:
            rel_sec = df_label[col].apply(time_str_to_sec) if col in df_label.columns else np.nan
            df_label[col] = meta_row['start_time'] + pd.to_timedelta(rel_sec, unit='s')

        df_label = df_label.reset_index(drop=True)
        df_label['Index']    = range(len(df_label))
        df_label['filename'] = meta_row['csv_filename']
        all_label_dfs.append(df_label[EXPECTED_COLUMNS].copy())

    if not all_label_dfs:
        raise RuntimeError(f"처리된 라벨 파일이 없습니다: {LABEL_DIR}")

    df_all = pd.concat(all_label_dfs, ignore_index=True).sort_values(by='filename')
    df_all.to_csv(ABS_LABEL_CSV, index=False, date_format=DATETIME_FORMAT)

    print(f"[DONE] {len(all_label_dfs)} label files → {ABS_LABEL_CSV}")
    if skipped:
        print(f"[WARN] 메타데이터(video_name) 매칭 실패 {len(skipped)}개: {skipped[:10]}{' ...' if len(skipped) > 10 else ''}")


if __name__ == "__main__":
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    run()
