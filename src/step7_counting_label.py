"""
STEP 7. counting용 샘플 단위 0/1 라벨

- step5의 정렬된 라벨(adjusted_labels_filtered.csv)의 시각을 센서 absolute_time에서 가장 가까운 샘플에 1로 찍음
    label        : Peak  (counting 학습 타깃, rep 1개당 1개)
    label_start  : Start
    label_end    : End
- 같은 샘플에 두 라벨이 겹치면 먼저 찍힌 것만 남김
- 녹화 구간 밖으로 벗어난 라벨 시각은 버림
- 정렬 라벨이 있는 파일만 포함 (라벨 없는 파일이 전부 0으로 학습에 섞이지 않도록)
- 센서는 COUNTING_SENSORS(3, 4)만, 1926Hz 그대로

입력
    data/interim/emg_imu_rms_abs.csv, unbalanced_merge.csv   (step3)
    data/output/adjusted_labels_filtered.csv                  (step5)

출력
    data/output/emg_imu_upsampled_sensor3_4.csv         (balance)
    data/output/emg_imu_upsampled_sensor3_4_unbal.csv   (unbalance)

실행 (preprocessing_1st/ 에서)
    python src/step7_counting_label.py
"""
import argparse
import numpy as np
import pandas as pd
from tqdm import tqdm

from config import *

LABEL_COLS = [('Peak', 'label'), ('Start', 'label_start'), ('End', 'label_end')]
META_COLS = ['filename', 'subject', 'exercise', 'is_1RM', 'weight_kg', 'rep', 'unbal_normalside']


def keep_column(col):
    """timestamp/절대시간/메타 + COUNTING_SENSORS 의 EMG, IMU, RMS 컬럼만"""
    if col in ('timestamp', 'absolute_time') or col in META_COLS:
        return True
    parts = col.split('_')
    return parts[0] in ('EMG', 'IMU') and len(parts) > 1 and parts[1].isdigit() and int(parts[1]) in COUNTING_SENSORS


def build_label_arrays(abs_time, label_rows):
    """파일 하나의 absolute_time(ns int64)에 대해 label / label_start / label_end 배열 생성"""
    n = len(abs_time)
    out = {name: np.zeros(n, dtype=np.int8) for _, name in LABEL_COLS}
    assigned = set()
    step = np.median(np.diff(abs_time)) if n > 1 else 0

    for _, row in label_rows.iterrows():
        for src, name in LABEL_COLS:
            t = row[src]
            if pd.isna(t):
                continue
            t = pd.Timestamp(t).value
            if t < abs_time[0] - step or t > abs_time[-1] + step:
                continue
            j = int(np.argmin(np.abs(abs_time - t)))
            if j in assigned:
                continue
            out[name][j] = 1
            assigned.add(j)
    return out


def run(group):
    src_path, out_path = MERGED_CSV[group], COUNTING_CSV[group]
    if not src_path.exists():
        print(f"[SKIP] {group}: {src_path} 없음 (step3 먼저 실행)")
        return
    if out_path.exists():
        out_path.unlink()

    labels = pd.read_csv(ADJUSTED_LABEL_CSV, parse_dates=['Start', 'Peak', 'End'])
    labels_by_file = {f: g.sort_values('Index') for f, g in labels.groupby('filename')}

    # step3 출력은 파일 단위로 연속 저장되어 있으므로, 청크를 읽으며 파일별 행 위치를 이어서 셈
    current, buf, header, n_files = None, [], True, 0

    def flush(fname, parts):
        nonlocal header, n_files
        df = pd.concat(parts, ignore_index=True)
        abs_ns = pd.to_datetime(df['absolute_time']).values.astype('datetime64[ns]').astype('int64')
        for name, arr in build_label_arrays(abs_ns, labels_by_file[fname]).items():
            df[name] = arr
        df.to_csv(out_path, mode='a', header=header, index=False)
        header = False
        n_files += 1

    reader = pd.read_csv(src_path, usecols=keep_column, chunksize=1_000_000)
    for chunk in tqdm(reader, desc=f"[{group}] chunks"):
        for fname, part in chunk.groupby('filename', sort=False):
            if fname != current:
                if current is not None and buf:
                    flush(current, buf)
                current, buf = fname, []
            if fname in labels_by_file:
                buf.append(part)
    if current is not None and buf:
        flush(current, buf)

    print(f"[DONE] {group}: {n_files} files (정렬 라벨이 있는 파일만) → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=GROUPS, default=None, help="미지정 시 balance, unbalance 모두 실행")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for group in ([args.group] if args.group else GROUPS):
        run(group)
