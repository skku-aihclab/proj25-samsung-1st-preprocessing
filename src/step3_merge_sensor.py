"""
STEP 3. 센서 CSV 병합 + 임시 절대시간 + pEMG RMS

- EMG(sensor 2~8, 1926Hz)는 원본 유지
- IMU(148Hz)는 EMG 시간축으로 선형보간 upsampling
- absolute_time = video_csv_info.xlsx의 start_time + EMG 상대시간  (아직 약 3초 오차 있음 → step5에서 보정)
- EMG_{i}_RMS: baseline 제거 → 10~500Hz bandpass → 정류 → 100ms 이동평균 → 200ms RMS

출력
    data/interim/emg_imu_rms_abs.csv       (balance)
    data/interim/unbalanced_merge.csv      (unbalance)
    data/interim/rms_cache/<filename>.npz  (step5 정렬용: absolute_time + RMS)

실행 (preprocessing_1st/ 에서)
    python src/step3_merge_sensor.py                  # balance, unbalance 모두
    python src/step3_merge_sensor.py --group balance  # 하나만
"""
import argparse
import os
import numpy as np
import pandas as pd
from tqdm import tqdm

from config import *
from common import load_meta, list_sensor_csvs, preprocess_pipeline, compute_rms_curve, rest_baseline_of


def process_file(df, filename, meta_row, sub, exercise):
    t_emg = df[EMG_TIME_COL].values

    # ── EMG 원본 유지 ──
    emg_data = {
        f'EMG_{i}': df[f'Trigno sensor {i}: EMG {i}'].values
        for i in SENSORS
        if f'Trigno sensor {i}: EMG {i}' in df.columns
    }
    if not emg_data:
        print(f"[SKIP] {filename} → EMG 컬럼 없음")
        return None

    # ── IMU 선형 보간 upsampling ──
    t_imu = df[IMU_TIME_COL].values
    upsampled_imu = {}
    for i in SENSORS:
        for axis in ['X', 'Y', 'Z']:
            col = f'Trigno sensor {i}: Acc {i}.{axis}'
            if col not in df.columns:
                continue
            v_raw = df[col].values
            idx_valid = ~np.isnan(v_raw)
            if idx_valid.sum() < 2:
                print(f"[SKIP] {filename} IMU_{i}_{axis} → 유효한 IMU 샘플 부족")
                continue
            upsampled_imu[f'IMU_{i}_{axis}'] = np.interp(t_emg, t_imu[idx_valid], v_raw[idx_valid])

    # ── 임시 절대시간 ──
    absolute_time = meta_row['start_time'] + pd.to_timedelta(t_emg, unit='s')

    # ── pEMG RMS ──
    pEMG_rms = {}
    for key, sig in emg_data.items():
        preprocessed = preprocess_pipeline(sig, rest_baseline_of(sig, EMG_FS), fs=EMG_FS, band=EMG_BAND)
        pEMG_rms[key + '_RMS'] = compute_rms_curve(preprocessed, fs=EMG_FS, window_ms=RMS_WINDOW_MS)

    # ── 결과 구조화 ──
    data = {
        'timestamp': t_emg,
        'absolute_time': absolute_time,
        **emg_data,
        **upsampled_imu,
        **pEMG_rms,
    }
    out = pd.DataFrame(data)
    out['filename']         = filename
    out['subject']          = sub
    out['exercise']         = exercise
    out['is_1RM']           = bool(meta_row['is_1RM'])
    out['weight_kg']        = meta_row['weight_kg']
    out['rep']              = meta_row['rep']
    out['unbal_normalside'] = meta_row['unbal_normalside']

    # step5용 경량 캐시
    np.savez_compressed(
        RMS_CACHE_DIR / f"{filename}.npz",
        absolute_time=absolute_time.values.astype('datetime64[ns]').astype('int64'),
        **{k: v for k, v in pEMG_rms.items()},
    )
    return out


def run(group, meta_df):
    out_path = MERGED_CSV[group]
    if out_path.exists():
        out_path.unlink()

    n_files, header = 0, True
    for exercise, file_path, filename, sub in tqdm(list_sensor_csvs(group, meta_df), desc=f"[{group}] files"):
        try:
            df = pd.read_csv(file_path)
            out = process_file(df, filename, meta_df.loc[filename], sub, exercise)
        except Exception as e:
            print(f"[ERROR] {filename}: {e}")
            continue
        if out is None:
            continue

        # 파일 단위로 이어쓰기 (전체를 메모리에 올리지 않음)
        out.to_csv(out_path, mode='a', header=header, index=False, date_format=DATETIME_FORMAT)
        header = False
        n_files += 1

    print(f"[DONE] {group}: {n_files} files → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=GROUPS, default=None, help="미지정 시 balance, unbalance 모두 실행")
    args = parser.parse_args()

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    RMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    meta_df = load_meta()
    for group in ([args.group] if args.group else GROUPS):
        run(group, meta_df)
