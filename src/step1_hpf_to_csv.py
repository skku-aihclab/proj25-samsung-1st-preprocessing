"""
STEP 1. Trigno 원본(.hpf) → CSV 변환 + Recording Date 요약표

1) CSV 변환 (Windows 전용)
   - EMGworks의 HPF.dll을 pythonnet으로 불러 채널별 데이터를 읽음
   - 채널마다 '<채널명>_time'(= 샘플번호 / 샘플레이트), '<채널명>' 두 열을 만듦
     → 'Trigno sensor 3: EMG 3_time', 'Trigno sensor 3: EMG 3', ...
   - EMG(1926Hz)와 Acc(148Hz)는 길이가 달라 짧은 채널의 뒤쪽은 NaN
   - 저장 위치: raw/<exercise>/<balance|unbalance>/<파일명>.csv  (step2~6이 바로 읽는 위치)

2) Recording Date 요약표 (OS 무관, 순수 Python)
   - hpf 파일 앞부분의 XML 헤더 <HeaderInfoV1><RecordingDate>를 읽음
     (※ <ChannelInformation><StartTime>은 AM/PM 없는 12시간제라 쓰면 안 됨: 14:34 → '02:34')
   - start_time = RecordingDate, end_time = start_time + duration_sec
   - 이 표의 start_time / end_time이 video_csv_info.xlsx의 start_time / end_time 원천

입력
    data/hpf/<날짜>/<balanced|unbalanced>/<exercise>/*.hpf

출력
    data/raw/<exercise>/<balance|unbalance>/*.csv
    data/meta/all_hpf_summary.csv   (file_name, start_time, end_time, duration_sec, date, type, exercise)

실행 (preprocessing_1st/ 에서)
    python src/step1_hpf_to_csv.py                  # 변환 + 요약표 (Windows)
    python src/step1_hpf_to_csv.py --summary-only   # 요약표만 (Mac 가능, duration은 변환된 CSV에서 계산)
    python src/step1_hpf_to_csv.py --overwrite      # 이미 있는 CSV도 다시 변환
"""
import argparse
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from tqdm import tqdm

from config import *

HEADER_READ_BYTES = 4096   # HeaderInfoV1 XML은 파일 앞 4KB 안에 있음


## hpf 목록
def iter_hpf_files():
    """hpf/<날짜>/<balanced|unbalanced>/<exercise>/*.hpf → (date, type, exercise, path)"""
    out = []
    for dirpath, _, filenames in os.walk(HPF_DIR):
        for f in filenames:
            if not f.lower().endswith('.hpf'):
                continue
            rel = os.path.relpath(dirpath, HPF_DIR).split(os.sep)
            if len(rel) < 3 or rel[1] not in HPF_GROUP_MAP:
                print(f"[SKIP] 폴더 구조가 <날짜>/<balanced|unbalanced>/<운동>/ 이 아님: {os.path.join(dirpath, f)}")
                continue
            out.append((rel[0], rel[1], rel[2], os.path.join(dirpath, f)))
    return sorted(out)


def csv_path_for(hpf_type, exercise, hpf_path):
    exercise = HPF_EXERCISE_FIX.get(exercise, exercise)
    name = os.path.splitext(os.path.basename(hpf_path))[0] + '.csv'
    return RAW_DIR / exercise / HPF_GROUP_MAP[hpf_type] / name


## Recording Date (순수 Python)
def read_recording_date(hpf_path):
    """
    hpf XML 헤더의 RecordingDate ('2025/06/04 14:34:24.0606115', 소수 7자리)를
    datetime으로 변환 (파이썬 datetime 정밀도에 맞춰 소수 6자리까지 사용)
    """
    with open(hpf_path, 'rb') as f:
        head = f.read(HEADER_READ_BYTES)
    m = re.search(rb'<RecordingDate>([^<]+)</RecordingDate>', head)
    if m is None:
        raise ValueError("RecordingDate를 찾을 수 없음")
    date_str, time_str = m.group(1).decode().strip().split(' ')
    hms, _, frac = time_str.partition('.')
    return datetime.strptime(f"{date_str} {hms}.{(frac + '000000')[:6]}", "%Y/%m/%d %H:%M:%S.%f")


## CSV 변환 (Windows + HPF.dll)
def load_hpf_reader():
    try:
        import clr
        clr.AddReference(HPF_DLL_PATH)
        from HPF import HPFReader
    except Exception as e:
        raise RuntimeError(
            f"HPF.dll 로드 실패: {e}\n"
            f"- Windows + EMGworks 설치 PC에서만 변환 가능 (DLL 경로: {HPF_DLL_PATH})\n"
            "- 'clr has no attribute AddReference' 오류면 다른 clr 패키지가 깔린 것: "
            "pip uninstall clr && pip install pythonnet"
        )
    return HPFReader


def hpf_to_csv(HPFReader, input_hpf, output_csv):
    """채널별 '<name>_time', '<name>' 열로 저장. 반환: 가장 긴 채널의 길이(초)"""
    reader = HPFReader(str(input_hpf))
    channel_names = [str(n) for n in reader.GetAllChannelNames()]
    sample_rates = [float(r) for r in reader.GetAllSampleRates()]
    data = OrderedDict()

    for i, name in enumerate(channel_names):
        try:
            samples = np.array(list(reader.GetData(i)), dtype=np.float64)
            data[f"{name}_time"] = np.arange(len(samples)) / sample_rates[i]
            data[name] = samples
        except Exception as e:
            print(f"[ERROR] {os.path.basename(input_hpf)} channel {i} - {name}: {e}")
            data[f"{name}_time"] = []
            data[name] = []

    if hasattr(reader, 'Close'):
        reader.Close()

    df = pd.DataFrame({k: pd.Series(v) for k, v in data.items()})
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    return duration_of(df)


def duration_of(df):
    """가장 긴 채널의 길이(초) = max(샘플 수 / 샘플레이트) = 마지막 timestamp + 1/fs"""
    durations = []
    for col in df.columns:
        if not col.endswith('_time'):
            continue
        t = df[col].dropna().values
        if len(t) >= 2:
            durations.append(t[-1] + (t[1] - t[0]))
    return max(durations) if durations else np.nan


def run(summary_only=False, overwrite=False):
    files = iter_hpf_files()
    if not files:
        raise RuntimeError(f"hpf 파일이 없습니다: {HPF_DIR}")

    HPFReader = None if summary_only else load_hpf_reader()
    rows = []
    for date, hpf_type, exercise, hpf_path in tqdm(files, desc="hpf"):
        out_csv = csv_path_for(hpf_type, exercise, hpf_path)
        row = {'file_name': out_csv.name, 'start_time': None, 'end_time': None, 'duration_sec': np.nan,
               'date': date, 'type': hpf_type, 'exercise': exercise}
        try:
            start = read_recording_date(hpf_path)
            row['start_time'] = start.isoformat()

            if not summary_only and (overwrite or not out_csv.exists()):
                duration = hpf_to_csv(HPFReader, hpf_path, out_csv)
            elif out_csv.exists():
                duration = duration_of(pd.read_csv(out_csv, usecols=lambda c: c.endswith('_time')))
            else:
                duration = np.nan

            if not np.isnan(duration):
                row['duration_sec'] = round(duration, 3)
                row['end_time'] = (start + timedelta(seconds=float(duration))).isoformat()
        except Exception as e:
            print(f"[ERROR] {hpf_path}: {e}")
        rows.append(row)

    summary = pd.DataFrame(rows)
    HPF_SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(HPF_SUMMARY_CSV, index=False)
    print(f"[DONE] hpf {len(files)}개 → 요약표 {HPF_SUMMARY_CSV}")
    if summary_only:
        print("[INFO] --summary-only: CSV 변환은 하지 않음")
    compare_with_meta(summary)


def compare_with_meta(summary):
    """새로 만든 요약표와 video_csv_info.xlsx의 start_time/end_time 비교 (검증용)"""
    if not META_PATH.exists():
        return
    meta = pd.read_excel(META_PATH)
    meta = meta[meta['csv_filename'].notna()]
    meta['k'] = meta['csv_filename'].astype(str).str.strip().str.lower()
    s = summary.assign(k=summary['file_name'].str.strip().str.lower())
    m = meta.merge(s, on='k', suffixes=('_meta', '_hpf'))
    if m.empty:
        print("[CHECK] 엑셀과 매칭되는 파일 없음")
        return
    d_start = (pd.to_datetime(m['start_time_hpf']) - pd.to_datetime(m['start_time_meta'])).dt.total_seconds().abs()
    d_end = (pd.to_datetime(m['end_time_hpf']) - pd.to_datetime(m['end_time_meta'])).dt.total_seconds().abs()
    print(f"[CHECK] 엑셀과 매칭 {len(m)}개 | start_time 1ms 이내 일치 {(d_start < 1e-3).sum()}개 "
          f"| end_time 1ms 이내 일치 {(d_end < 1e-3).sum()}개 (end_time 비교는 CSV가 있을 때만)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-only", action="store_true", help="CSV 변환 없이 Recording Date 요약표만 (Mac 가능)")
    parser.add_argument("--overwrite", action="store_true", help="이미 변환된 CSV도 다시 변환")
    args = parser.parse_args()
    run(summary_only=args.summary_only, overwrite=args.overwrite)
