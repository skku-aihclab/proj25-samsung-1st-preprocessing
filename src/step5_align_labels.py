"""
STEP 5. 영상 라벨 ↔ EMG 시간축 정렬 (offset 보정)  ★ 핵심 단계

1) Peak 후보   : 센서(2~8)별 RMS에서 운동별 최소 간격(PEAK_DISTANCE_SEC)으로 find_peaks, 라벨 개수만큼 선택
2) Best sensor : 라벨 Peak interval vs 센서 peak interval 오차제곱 중 작은 top-k 평균이 최소인 센서
3) Offset      : 연속 interval 2개의 MSE가 최소인 i → offset = label[i] - sensor[i]
4) 적용        : 파일별 상수 offset을 Start/Peak/End에서 뺌
5) 제외        : config.EXCLUDED_FILES (검수 그림에서 정렬 실패로 판단한 파일)

입력
    data/interim/absolute_labels.csv   (step4)
    data/interim/rms_cache/*.npz       (step3, 큰 병합 CSV 대신 파일별 RMS 캐시를 읽음)

출력
    data/output/adjusted_labels_filtered.csv   (filename, Index, Start, Peak, End)  ← 최종 정렬 라벨
    data/output/alignment_summary.csv          (파일별 best_sensor, offset_sec, anchor_idx, excluded)
    data/interim/peak_visualization/*.png      (--plot 옵션 시, 파일별 검수 그림)

실행 (preprocessing_1st/ 에서)
    python src/step5_align_labels.py
    python src/step5_align_labels.py --plot     # 검수 그림도 저장
"""
import argparse
import os
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from tqdm import tqdm

from config import *
from common import load_meta, iter_raw_csvs


## 데이터 로딩
def load_rms_cache(filename):
    path = RMS_CACHE_DIR / f"{filename}.npz"
    if not path.exists():
        return None
    z = np.load(path)
    abs_time = z['absolute_time'].astype('datetime64[ns]')
    rms = {int(k.split('_')[1]): z[k] for k in z.files if k.endswith('_RMS')}
    return abs_time, rms


def load_exercise_map():
    """csv_filename → exercise (raw/ 폴더 기준. 엑셀 exercise 표기는 lpd 등 제각각이라 쓰지 않음)"""
    return {key: exercise for group in GROUPS for exercise, _, key in iter_raw_csvs(group)}


## 1) 센서 RMS에서 peak 후보 추출
def get_sensor_peak_candidates(abs_time, rms_dict, exercise, n_peaks_required,
                               prominence_ratio=PROMINENCE_RATIO, width_sec=PEAK_WIDTH_SEC):
    """
    라벨 개수에 맞춰 sensor RMS peak 후보를 조건 기반으로 추출.
    부족할 경우 조건을 완화해서라도 count를 채움.

    Returns:
        {sensor_num: peak_times (datetime64 array)}
    """
    min_distance = int(PEAK_DISTANCE_SEC[exercise] * EMG_FS)
    min_width = int(width_sec * EMG_FS)

    sensor_peaks = {}
    for sensor_num in SENSORS:
        if sensor_num not in rms_dict:
            continue
        emg_rms = rms_dict[sensor_num]
        min_prominence = np.max(emg_rms) * prominence_ratio

        # 초기 조건으로 find_peaks
        peaks, props = find_peaks(emg_rms, distance=min_distance, prominence=min_prominence, width=min_width)

        # 정렬 우선순위: prominence > height
        if len(peaks) > 0:
            sort_idx = np.lexsort((-emg_rms[peaks], -props["prominences"]))
            peaks = peaks[sort_idx]

        # 조건 미달 시: distance만 적용해 전체 후보 확보
        if len(peaks) < n_peaks_required:
            all_peaks, _ = find_peaks(emg_rms, distance=min_distance)
            remaining = [p for p in all_peaks if p not in peaks]
            remaining = sorted(remaining, key=lambda i: -emg_rms[i])[:n_peaks_required - len(peaks)]
            peaks = np.concatenate([peaks, remaining]).astype(int)

        # 그래도 부족하면 가장 높은 것부터 채우기 (마지막 보완)
        if len(peaks) < n_peaks_required:
            peaks = np.sort(np.argsort(-emg_rms)[:n_peaks_required])

        selected_peaks = np.sort(peaks[:n_peaks_required])
        sensor_peaks[sensor_num] = abs_time[selected_peaks]

    return sensor_peaks


## 2) 간격 오차 기반 best sensor
def compute_interval_error(label_times, sensor_times, k=INTERVAL_TOP_K):
    """
    라벨/센서 peak 시퀀스의 interval 오차제곱 중 작은 top-k 평균 (ms^2).
    peak가 1개뿐이면 절대시간 차이의 제곱으로 대체.
    """
    label_times = np.sort(label_times)
    sensor_times = np.sort(sensor_times)

    if len(label_times) < 2 or len(sensor_times) < 2:
        if len(label_times) == 1 and len(sensor_times) == 1:
            delta = (label_times[0] - sensor_times[0]) / np.timedelta64(1, 'ms')
            return float(delta ** 2)
        return np.inf

    label_intervals = np.diff(label_times).astype('timedelta64[ms]').astype(float)
    sensor_intervals = np.diff(sensor_times).astype('timedelta64[ms]').astype(float)

    min_len = min(len(label_intervals), len(sensor_intervals))
    if min_len == 0:
        return np.inf

    errors = (label_intervals[:min_len] - sensor_intervals[:min_len]) ** 2
    return np.mean(np.sort(errors)[:min(k, len(errors))])


def find_best_sensor_by_interval(label_times, sensor_peak_dict, k=INTERVAL_TOP_K):
    best_sensor, min_error = None, np.inf
    for sensor_num, peak_times in sensor_peak_dict.items():
        error = compute_interval_error(label_times, peak_times, k=k)
        if error < min_error:
            min_error, best_sensor = error, sensor_num
    return best_sensor


## 3) offset 계산
def find_best_offset_by_two_interval_mse(label_times, sensor_times):
    """
    label[i:i+3]과 sensor[i:i+3]의 2개 interval을 비교 (MSE 기반)
    Returns:
        best_offset (sec), best_idx  (peak가 2개뿐이면 비교 불가 → offset 0.0, idx -1)
    """
    label_ts = label_times.astype("datetime64[ns]").astype("int64") / 1e9
    sensor_ts = sensor_times.astype("datetime64[ns]").astype("int64") / 1e9

    min_len = min(len(label_ts), len(sensor_ts))
    best_offset, min_mse, best_idx = 0.0, float('inf'), -1

    for i in range(min_len - 2):
        l1, l2 = label_ts[i+1] - label_ts[i], label_ts[i+2] - label_ts[i+1]
        s1, s2 = sensor_ts[i+1] - sensor_ts[i], sensor_ts[i+2] - sensor_ts[i+1]
        mse = ((l1 - s1)**2 + (l2 - s2)**2) / 2
        if mse < min_mse:
            min_mse, best_offset, best_idx = mse, label_ts[i] - sensor_ts[i], i

    return best_offset, best_idx


## 검수 그림
def plot_best_sensor(fname, abs_time, rms_dict, best_sensor, peak_times, anchor_idx, aligned_peaks, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.figure(figsize=(14, 5))
    plt.plot(abs_time, rms_dict[best_sensor], color='tab:blue', alpha=0.7, linewidth=1.0, label=f"EMG_{best_sensor}_RMS")
    # 센서 peak 후보: 파랑 점선, anchor: 빨강 실선
    for i, pt in enumerate(peak_times):
        is_anchor = i == anchor_idx
        plt.axvline(pt, color='red' if is_anchor else 'blue', linestyle='-' if is_anchor else '--',
                    alpha=0.8, linewidth=1.5 if is_anchor else 1.0)
    # 정렬된 라벨 Peak: 초록
    for pt in aligned_peaks:
        plt.axvline(pt, color='green', alpha=0.5, linewidth=3)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(PLOT_DIR / f"{fname}_sensor_{best_sensor}_aligned.png", dpi=150)
    plt.close()


def run(save_plot=False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if save_plot:
        PLOT_DIR.mkdir(parents=True, exist_ok=True)

    label_df = pd.read_csv(ABS_LABEL_CSV, parse_dates=['Start', 'Peak', 'End'])
    label_peak_counts = label_df.groupby('filename').size().to_dict()
    meta_df = load_meta()
    ex_map = load_exercise_map()

    summary, offsets = [], {}
    for fname in tqdm(label_df['filename'].unique(), desc="Aligning"):
        row = {'filename': fname, 'exercise': ex_map.get(fname), 'status': ''}
        cache = load_rms_cache(fname)
        if cache is None or row['exercise'] not in PEAK_DISTANCE_SEC:
            row['status'] = 'no_sensor_data'
            summary.append(row)
            continue
        abs_time, rms_dict = cache

        label_sub = label_df[label_df['filename'] == fname].sort_values('Index')
        label_peaks = label_sub['Peak'].dropna().values
        if len(label_peaks) == 0:
            row['status'] = 'no_label_peak'
            summary.append(row)
            continue

        sensor_peak_dict = get_sensor_peak_candidates(abs_time, rms_dict, row['exercise'], label_peak_counts[fname])

        # 1. Best sensor
        best_sensor = find_best_sensor_by_interval(label_peaks, sensor_peak_dict)
        if best_sensor is None:
            row['status'] = 'no_best_sensor'
            summary.append(row)
            continue
        sensor_peaks = sensor_peak_dict[best_sensor]

        # 2. Offset (+ 단일 peak 예외 처리)
        if len(label_peaks) == 1 or len(sensor_peaks) == 1:
            offset = (label_peaks[0].astype('datetime64[ns]').astype('int64') -
                      sensor_peaks[0].astype('datetime64[ns]').astype('int64')) / 1e9
            anchor_idx = 0
        else:
            offset, anchor_idx = find_best_offset_by_two_interval_mse(label_peaks, sensor_peaks)

        offsets[fname] = offset
        row.update({
            'best_sensor': best_sensor,
            'offset_sec': offset,
            'anchor_idx': anchor_idx,
            'n_label_peaks': len(label_peaks),
            'excluded': fname in EXCLUDED_FILES,
            'status': 'ok' if anchor_idx >= 0 else 'offset_not_computed(peak<3)',
        })
        summary.append(row)

        if save_plot:
            m = meta_df.loc[fname] if fname in meta_df.index else None
            title = (f"{fname} | {row['exercise']} | "
                     f"{'' if m is None else m['weight_kg']}kg | Sensor {best_sensor} | offset {offset:.3f}s")
            aligned_peaks = label_peaks - np.timedelta64(int(offset * 1e9), 'ns')
            plot_best_sensor(fname, abs_time, rms_dict, best_sensor, sensor_peaks, anchor_idx, aligned_peaks, title)

    # 3. offset 적용
    adjusted_df = label_df.copy()
    for fname, offset_sec in offsets.items():
        offset_delta = np.timedelta64(int(offset_sec * 1e9), 'ns')
        mask = adjusted_df['filename'] == fname
        for col in ['Start', 'Peak', 'End']:
            adjusted_df.loc[mask, col] = adjusted_df.loc[mask, col] - offset_delta

    # 4. 정렬 실패 파일 제외
    adjusted_df = adjusted_df[~adjusted_df['filename'].isin(EXCLUDED_FILES)]
    # 센서 데이터가 없어 offset을 못 구한 파일도 제외 (임시 절대시간 그대로 남지 않도록)
    adjusted_df = adjusted_df[adjusted_df['filename'].isin(offsets.keys())]

    adjusted_df.to_csv(ADJUSTED_LABEL_CSV, index=False, date_format=DATETIME_FORMAT)
    pd.DataFrame(summary).to_csv(ALIGN_SUMMARY_CSV, index=False)

    summary_df = pd.DataFrame(summary)
    print(f"[DONE] {adjusted_df['filename'].nunique()} files → {ADJUSTED_LABEL_CSV}")
    print(summary_df['status'].value_counts().to_string())
    print(f"[INFO] 파일별 결과 → {ALIGN_SUMMARY_CSV}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="파일별 검수 그림 저장 (data/interim/peak_visualization)")
    args = parser.parse_args()
    run(save_plot=args.plot)
