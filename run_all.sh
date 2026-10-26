#!/usr/bin/env bash
# 1차 데이터 전처리 전체 실행 (preprocessing_1st/ 에서: bash run_all.sh)
# 입력: data/raw (NAS CSV), data/labels, data/meta
# step1(hpf → CSV)은 NAS CSV를 만든 참고용 변환 코드라 포함하지 않음
set -e
cd "$(dirname "$0")"
python src/step2_check_inputs.py
python src/step3_merge_sensor.py
python src/step4_label_abs_time.py
python src/step5_align_labels.py --plot
python src/step6_downsample.py
python src/step7_counting_label.py
