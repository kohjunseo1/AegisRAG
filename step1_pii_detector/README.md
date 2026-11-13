# Step1 — 개인정보 탐지

Step1은 금융 문서와 사용자 질의에서 개인정보의 **유형(label)** 및 **문자 구간([start, end))**을 추출하는 로컬 탐지 모듈입니다. 탐지 결과는 Step2의 Privacy Gate와 HMAC 가명처리에 전달됩니다.

## 탐지 모델 및 구성

| 유형 | 모델 |
|---|---|
| PERSON, PHONE_NUMBER, EMAIL | A.X |
| RESIDENT_REGISTRATION_NUMBER, ACCOUNT_NUMBER, CARD_NUMBER | A.X |
| ADDRESS | Veil |

- **A.X:** `PII-detect/pii-ax-mixed-v1` (PyTorch)
- **Veil:** `PII-detect/pii-veil-mixed-v1` (ONNX Runtime)
- 고정 모델 revision 및 라벨별 담당 모델은 [model_manifest.json](src/pii_detector/model_manifest.json)을 확인하세요.
- `detector.py`는 라벨별 결합·충돌 정리, 주소/우편번호 경계 검증, 긴 문서 처리, 실패 상태 처리를 담당합니다.
- `structural_policy.py`는 명시적 성씨+직함 및 짧은 내선번호처럼 **Strict 평가 구간에서 제외해도 보호 구간에는 남겨야 하는 후보**를 다룹니다.

## 실행 흐름과 반환 계약

```text
입력 텍스트
  → A.X / Veil 예측
  → 라벨별 결합·경계 검증·Structural Policy
  → entities + mask_intervals + status
  → Step2 Privacy Gate
```

핵심 반환 값:

| 필드 | 의미 |
|---|---|
| `entities` | 정확한 라벨·경계를 평가하거나 구조화할 때 쓰는 Strict 구간 |
| `mask_intervals` | 실제 보호 대상 문자 구간. `entities`보다 넓을 수 있음 |
| `status` | `READY_FOR_PSEUDONYMIZATION` / `REVIEW_REQUIRED` / `BLOCKED` |

**가명처리는 `entities`만 보고 보호 범위를 축소하면 안 됩니다.** `READY_FOR_PSEUDONYMIZATION`도 외부 LLM 전송 허가는 아닙니다.

합성 예제로 로컬 모델 호출:

```python
from pii_detector import create_detector

with create_detector() as detector:
    result = detector.detect("홍길동 고객의 연락처는 010-1234-5678입니다.")
    print(result["status"])
    print(result["entities"])
    print(result["mask_intervals"])
```

모델 접근에는 두 비공개 Hugging Face 저장소에 대한 **개인 계정 권한**과 `HF_TOKEN`이 필요합니다. 설치·환경변수는 [루트 README](../README.md)를 참고하세요. 실제 인물·문서 원문을 진단 로그나 Git에 넣지 마세요.

## 평가 결과와 해석

| 데이터·시점 | Strict F1 | Mask Recall |
|---|---:|---:|
| 최초 동결 Hybrid(05) — Official Test 200문서 | 99.23% | 99.97% |
| 최초 동결 Hybrid(05) — Official Holdout 187문서 | 97.73% | 99.48% |
| 현재 Structural/주소 후처리 — Holdout 저장 예측 **사후 재채점** | 99.3450% | 100.00% |

마지막 행은 Holdout을 분석한 이후 기존 저장 예측에 정책을 적용한 **오프라인 재채점**으로, 현재 실모델 추론 전체의 새 Blind 성능을 의미하지 않습니다. 상세 조건과 이전 정책 선택 기록은 [실험 이력](../docs/EXPERIMENT_HISTORY.md)에 있습니다.

## 테스트·구조

```bash
PYTHONPATH=step1_pii_detector:step1_pii_detector/src \
python -m unittest discover -s step1_pii_detector/tests -v
```

최근 정리 기준 Step1 29건 통과, 과거 reference notebook이 없어 비교 1건은 별도로 실행되지 않았습니다. CI 합성 테스트와 실제 비공개 모델 재현은 구분해야 합니다.

```text
src/pii_detector/
  factory.py            탐지기 생성·모델 고정
  loader.py, backends.py 모델 로딩·추론 어댑터
  detector.py           라벨 결합·경계·상태 처리
  structural_policy.py  보호 우선 구조 정책
  model_manifest.json   모델 revision·라벨 라우팅
tests/                  동작·경계·회귀 테스트
evaluation/             저장 예측의 오프라인 비교·재채점
```

이 탐지기를 기준으로 Step2에서는 [Privacy Gate → 가명처리 → 검색](../step2_privacy_rag/README.md)을 수행합니다.
