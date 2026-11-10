# Step1 PII detector

A.X는 PERSON/전화/이메일/주민번호/계좌/카드, Veil은 주소를 담당합니다. 모델 revision은 `src/pii_detector/model_manifest.json`에 고정되어 있습니다. 기존 Structural Policy와 탐지 경계는 유지합니다.

`create_detector()`와 `detect(text)`는 `entities`, `mask_intervals`, `status`를 반환합니다. `entities`는 Strict 평가 구간, `mask_intervals`는 실제 보호 구간입니다. `READY_FOR_PSEUDONYMIZATION`은 외부 전송 허가가 아닙니다. `REVIEW_REQUIRED`/`BLOCKED`를 상위 파이프라인에서 보존해야 합니다.

설치·모델 접근·실행은 [루트 Quick Start](../README.md), 모델/DB 교체는 [재현 문서](../docs/REPRODUCIBILITY.md), 최초 blind Test/Holdout과 사후 재채점 수치는 [실험 이력](../docs/EXPERIMENT_HISTORY.md)을 참고하세요.

회귀 테스트는 저장소 루트에서:

```bash
PYTHONPATH=step1_pii_detector:step1_pii_detector/src python -m unittest discover -s step1_pii_detector/tests -v
```

`evaluation/offline_regression.py`는 저장 예측의 오프라인 재채점용이며 실제 detector 추론 성능과 구분합니다. 비공개 원본·Gold·예측 파일은 Git 밖 또는 제외된 `local_eval/`에 보관하고 명시적 경로로 평가기에 전달합니다. 과거 reference notebook 부재로 parity 비교 1건은 실행 불가임을 테스트가 표시합니다.
