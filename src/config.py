"""
1차 데이터 전처리 공통 설정 (경로, 샘플링, 운동별 파라미터)
모든 경로는 preprocessing_1st/ 폴더 기준으로 자동 계산됨
"""
from pathlib import Path

# ── 경로 ──
ROOT_DIR     = Path(__file__).resolve().parent.parent
DATA_DIR     = ROOT_DIR / "data"

HPF_DIR      = DATA_DIR / "hpf"              # Trigno 원본: hpf/<날짜>/<balanced|unbalanced>/<exercise>/*.hpf
RAW_DIR      = DATA_DIR / "raw"              # 센서 CSV: raw/<exercise>/<balance|unbalance>/*.csv
LABEL_DIR    = DATA_DIR / "labels"           # 영상 라벨: labels/<video_name>.csv
META_PATH    = DATA_DIR / "meta" / "video_csv_info.xlsx"
INTERIM_DIR  = DATA_DIR / "interim"          # 중간 산출물
OUTPUT_DIR   = DATA_DIR / "output"           # 최종 산출물

RMS_CACHE_DIR = INTERIM_DIR / "rms_cache"    # 파일별 RMS + 절대시간 (step5 정렬용)
PLOT_DIR      = INTERIM_DIR / "peak_visualization"

# ── hpf → csv 변환 (step1) ──
# EMGworks 설치 시 함께 깔리는 Delsys 변환 라이브러리 (Windows 전용)
HPF_DLL_PATH     = r"C:\Program Files (x86)\Delsys, Inc\EMGworks\Matlab Conversion Library\HPF.dll"
HPF_SUMMARY_CSV  = DATA_DIR / "meta" / "all_hpf_summary.csv"   # 파일별 Recording Date → video_csv_info.xlsx start_time
HPF_GROUP_MAP    = {"balanced": "balance", "unbalanced": "unbalance"}
HPF_EXERCISE_FIX = {"deadlfit": "deadlift", "barbelrow": "barbellrow"}   # 수집 당시 폴더명 오타

# ── 산출물 파일명 ──
MERGED_CSV = {
    "balance":   INTERIM_DIR / "emg_imu_rms_abs.csv",
    "unbalance": INTERIM_DIR / "unbalanced_merge.csv",
}
ABS_LABEL_CSV       = INTERIM_DIR / "absolute_labels.csv"
ALIGN_SUMMARY_CSV   = OUTPUT_DIR / "alignment_summary.csv"
ADJUSTED_LABEL_CSV  = OUTPUT_DIR / "adjusted_labels_filtered.csv"
DOWNSAMPLED_CSV = {
    "balance":   OUTPUT_DIR / "emg_imu_downsampled_sensor3_4.csv",
    "unbalance": OUTPUT_DIR / "emg_imu_downsampled_sensor3_4_unbal.csv",
}
COUNTING_CSV = {
    "balance":   OUTPUT_DIR / "emg_imu_upsampled_sensor3_4.csv",
    "unbalance": OUTPUT_DIR / "emg_imu_upsampled_sensor3_4_unbal.csv",
}

# ── 데이터 구성 ──
EXERCISES = [
    'deadlift', 'barbellrow', 'latpulldown', 'benchpress',
    'barbellcurl', 'bte', 'ohp', 'dips', 'pushup', 'pullup'
]
GROUPS    = ["balance", "unbalance"]
SENSORS   = list(range(2, 9))                # Trigno sensor 2~8

# ── 샘플링 ──
EMG_FS = 1926
IMU_FS = 148

# 시간축은 모든 센서가 sensor 3의 time 컬럼을 공용으로 사용
EMG_TIME_COL = 'Trigno sensor 3: EMG 3_time'
IMU_TIME_COL = 'Trigno sensor 3: Acc 3.X_time'

# ── pEMG ──
EMG_BAND      = (10, 500)
RMS_WINDOW_MS = 200

# ── 정렬 (step5) ──
# 운동별 peak 최소 간격(초). 영상 라벨의 rep 간격을 참고해 설정
PEAK_DISTANCE_SEC = {
    'barbellcurl': 1.5,
    'barbellrow':  0.5,
    'benchpress':  1.0,
    'bte':         1.5,
    'deadlift':    3.0,
    'latpulldown': 1.5,
    'ohp':         1.5,
    'dips':        1.5,
    'pullup':      1.5,
    'pushup':      0.5,
}
PROMINENCE_RATIO = 0.1     # prominence >= max(RMS) * ratio
PEAK_WIDTH_SEC   = 0.05
INTERVAL_TOP_K   = 3       # best sensor 선정 시 사용할 interval 오차 개수

# 검수 그림에서 정렬 실패로 판단해 제외한 파일 (파일명 오타 deadllift 포함, 실제 파일명 그대로)
EXCLUDED_FILES = [
    "sub1_deadlift_50_plot_and_store_rep_1.1.csv",
    "sub1_deadlift_65_plot_and_store_rep_1.2.csv",
    "sub5_deadlift_plot_and_store_rep_1.93.csv",
    "sub5_deadlift_plot_and_store_rep_2.94.csv",
    "sub8_deadllift_plot_and_store_rep_1.40.csv",
    "sub8_deadllift_plot_and_store_rep_1.44.csv",
    "sub9_deadlift_plot_and_store_rep_2.65.csv",
    "sub12_bte_plot_and_store_rep_5.4.csv",
    "sub8_ohp_plot_and_store_rep_2.46.csv",
]

# ── 다운샘플 (step6) ──
DOWNSAMPLE_FS      = 148
DOWNSAMPLE_SENSORS = (3, 4, 6, 7)   # 불균형: 왼 이두·삼두(3,4) + 오른 이두·삼두(6,7). 균형에서 6,7은 왼 승모·광배

# ── counting 라벨 (step7) ──
COUNTING_SENSORS = (3, 4)            # 왼 이두·삼두 (균형·불균형 공통 상완 채널)

DATETIME_FORMAT ="%Y-%m-%d %H:%M:%S.%f"
