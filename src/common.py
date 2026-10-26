"""
공통 함수: 메타데이터/센서 CSV 로딩, pEMG 전처리, 시간 문자열 변환
"""
import os
import unicodedata
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

from config import META_PATH, RAW_DIR, EXERCISES


## 파일명 정규화
def normalize_filename(name):
    """
    파일명 매칭 키: strip → (깨진 한글 복원) → NFC → 소문자
    - NAS 복사 과정에서 UTF-8 한글이 cp1252로 깨진 이름(예: 'á„Žá…®á„€á…¡' → '추가')을 복원
    - macOS 파일명(NFD)과 엑셀 문자열(NFC)의 한글 표현 차이 통일
    """
    s = str(name).strip()
    try:
        s = s.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unicodedata.normalize('NFC', s).lower()


## 메타데이터
def load_meta(meta_path=META_PATH):
    """
    video_csv_info.xlsx 로딩 후 1RM / weight_ratio 계산

    Returns:
        meta_df: csv_filename(소문자) 기준 메타 DataFrame
    """
    df = pd.read_excel(meta_path)

    required_cols = ['csv_filename', 'video_name', 'start_time', 'subject_id', 'exercise', 'weight_kg', 'rep']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"엑셀 파일에 '{col}' 열이 필요합니다.")

    df = df[df['csv_filename'].notna()].copy()
    df['csv_filename'] = df['csv_filename'].map(normalize_filename)
    df['video_name_base'] = df['video_name'].astype(str).str.strip().str.replace(".mp4", "", case=False)
    df['start_time'] = pd.to_datetime(df['start_time'])
    df['subject_id'] = df['subject_id'].astype(str).str.strip()
    df['exercise'] = df['exercise'].astype(str).str.strip()
    df['weight_kg'] = pd.to_numeric(df['weight_kg'], errors='coerce')
    df['rep'] = pd.to_numeric(df['rep'], errors='coerce')
    df['unbal_normalside'] = df['unbal_normalside'].where(
        df['unbal_normalside'].isna(), df['unbal_normalside'].astype(str).str.strip().str.lower())

    # ---------------------- 1RM 계산 -------------------------
    df['is_1RM'] = df['rep'] == 1
    one_rm_rep = df[df['is_1RM']].groupby(['subject_id', 'exercise'])['weight_kg'].max().rename('1rm_weight')
    max_weight = df.groupby(['subject_id', 'exercise'])['weight_kg'].max().rename('max_weight')
    one_rm = pd.concat([one_rm_rep, max_weight], axis=1)
    one_rm['1rm_weight'] = one_rm['1rm_weight'].fillna(one_rm['max_weight'])
    df = df.merge(one_rm[['1rm_weight']].reset_index(), on=['subject_id', 'exercise'], how='left')
    df['weight_ratio'] = df['weight_kg'] / df['1rm_weight']

    return df.drop_duplicates('csv_filename').set_index('csv_filename')


def iter_raw_csvs(group):
    """
    raw/<운동>/<group>/ 아래 모든 CSV를 (exercise, file_path, key) 로 반환
    key = normalize_filename(파일명). 운동 → 경로 순으로 정렬
    """
    out = []
    for exercise in EXERCISES:
        for dirpath, _, filenames in os.walk(RAW_DIR / exercise / group):
            for filename in filenames:
                if filename.lower().endswith('.csv'):
                    out.append((exercise, os.path.join(dirpath, filename), normalize_filename(filename)))
    return sorted(out, key=lambda x: (EXERCISES.index(x[0]), x[1]))


def list_sensor_csvs(group, meta_df):
    """
    메타(엑셀)에 등록된 센서 CSV만 (exercise, file_path, key, subject) 로 반환
    subject는 엑셀 subject_id 기준. normal_data == 'x'(실패·삭제 데이터)는 제외
    """
    out = []
    for exercise, path, key in iter_raw_csvs(group):
        if key not in meta_df.index:
            continue
        if str(meta_df.loc[key, 'normal_data']).strip().lower() == 'x':
            continue
        out.append((exercise, path, key, f"sub{meta_df.loc[key, 'subject_id']}"))
    return out


## 시간 변환
def time_str_to_sec(time_str):
    """'mm:ss.sss' → 초(float)"""
    if pd.isna(time_str):
        return np.nan
    try:
        minute, second = str(time_str).strip().split(":")
        return int(minute) * 60 + float(second)
    except Exception:
        return np.nan


## pEMG preprocessing
def preprocess_pipeline(signal, rest_baseline, fs=1926, band=(10, 500)):
    """baseline 제거 → bandpass → 정류 → 100ms 이동평균"""
    if np.any(np.isnan(signal)):
        signal = pd.Series(signal).interpolate(limit_direction='both').bfill().ffill().values

    original_len = len(signal)
    signal = signal - rest_baseline

    # Bandpass filter
    nyq = 0.5 * fs
    low, high = band[0] / nyq, band[1] / nyq
    b, a = butter(4, [low, high], btype='band')
    try:
        filtered = filtfilt(b, a, signal)
    except ValueError:
        filtered = signal

    rectified = np.abs(filtered)

    smoothing_window = int(fs * 0.1)
    smoothed = np.convolve(rectified, np.ones(smoothing_window) / smoothing_window, mode='same')

    if len(smoothed) != original_len:
        smoothed = smoothed[:original_len] if len(smoothed) > original_len else np.pad(smoothed, (0, original_len - len(smoothed)), mode='edge')

    return smoothed


def compute_rms_curve(signal, fs, window_ms=200):
    """제곱 → 이동평균 → sqrt (window_ms 단위 RMS)"""
    window_size = int(fs * window_ms / 1000)
    if window_size < 1:
        raise ValueError("Window size too small for given fs and window_ms")

    squared = signal ** 2
    kernel = np.ones(window_size) / window_size
    moving_avg = np.convolve(squared, kernel, mode='same')
    return np.sqrt(moving_avg)


def rest_baseline_of(sig, fs):
    """첫 1초 평균 (1초 미만이면 앞 10%)"""
    if len(sig) >= fs:
        return float(np.nanmean(sig[:fs]))
    return float(np.nanmean(sig[:max(1, len(sig) // 10)]))
