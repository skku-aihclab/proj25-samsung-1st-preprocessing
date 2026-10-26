"""
STEP 6. 모델 입력용 148Hz 다운샘플 (불균형 분류, 운동 분류 등)

- 센서 3, 4, 6, 7만 사용 (파일명은 관례상 sensor3_4)
- EMG 1926Hz → 148Hz: scipy.signal.decimate(factor=13, IIR)
- IMU: NaN 제거 후 다운샘플된 시간축에 nearest neighbor 매칭

출력
    data/output/emg_imu_downsampled_sensor3_4.csv         (balance)
    data/output/emg_imu_downsampled_sensor3_4_unbal.csv   (unbalance)

실행 (preprocessing_1st/ 에서)
    python src/step6_downsample.py
    python src/step6_downsample.py --group unbalance
"""
import argparse
import os
import numpy as np
import pandas as pd
from scipy.signal import decimate
from tqdm import tqdm

from config import *
from common import load_meta, list_sensor_csvs

EMG_FACTOR = int(round(EMG_FS / DOWNSAMPLE_FS))


def process_file(df, filename, meta_row, sub, exercise):
    # ── EMG 다운샘플 ──
    t_emg = df[EMG_TIME_COL].values
    emg_data = {
        f'EMG_{i}': df[f'Trigno sensor {i}: EMG {i}'].values
        for i in DOWNSAMPLE_SENSORS
        if f'Trigno sensor {i}: EMG {i}' in df.columns
    }
    if not emg_data:
        return None

    down_emg = {ch: decimate(sig, EMG_FACTOR, ftype='iir') for ch, sig in emg_data.items()}
    t_ds = t_emg[::EMG_FACTOR]
    t_ds = t_ds[:len(next(iter(down_emg.values())))]

    # ── IMU 다운샘플 (NaN-safe Nearest Neighbor) ──
    t_imu = df[IMU_TIME_COL].values
    down_imu = {}
    for i in DOWNSAMPLE_SENSORS:
        for axis in ['X', 'Y', 'Z']:
            col = f'Trigno sensor {i}: Acc {i}.{axis}'
            if col not in df.columns:
                continue
            v_raw = df[col].values
            idx_valid = ~np.isnan(v_raw)
            if idx_valid.sum() == 0:
                print(f"[SKIP] {filename} IMU_{i}_{axis} → NaN 제외 후 유효한 IMU 없음")
                continue
            v_valid, t_valid = v_raw[idx_valid], t_imu[idx_valid]
            idx = np.abs(t_valid[:, None] - t_ds[None, :]).argmin(axis=0)
            down_imu[f'IMU_{i}_{axis}'] = v_valid[idx]

    out = pd.DataFrame({'timestamp': t_ds, **down_emg, **down_imu})
    out['filename']         = filename
    out['subject']          = sub
    out['exercise']         = exercise
    out['is_1RM']           = bool(meta_row['is_1RM'])
    out['weight_kg']        = meta_row['weight_kg']
    out['rep_id']           = meta_row['rep']
    out['unbal_normalside'] = meta_row['unbal_normalside']
    return out


def run(group, meta_df):
    out_path = DOWNSAMPLED_CSV[group]
    if out_path.exists():
        out_path.unlink()

    n_files, header = 0, True
    for exercise, file_path, filename, sub in tqdm(list_sensor_csvs(group, meta_df), desc=f"[{group}] files"):
        try:
            out = process_file(pd.read_csv(file_path), filename, meta_df.loc[filename], sub, exercise)
        except Exception as e:
            print(f"[ERROR] {filename}: {e}")
            continue
        if out is None:
            continue
        out.to_csv(out_path, mode='a', header=header, index=False)
        header = False
        n_files += 1

    print(f"[DONE] {group}: {n_files} files → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=GROUPS, default=None, help="미지정 시 balance, unbalance 모두 실행")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    meta_df = load_meta()
    for group in ([args.group] if args.group else GROUPS):
        run(group, meta_df)
