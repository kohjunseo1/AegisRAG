# Step 1 — PII Detector

한국어 금융 문서에서 개인정보 7종의 span을 탐지하고 상태를 반환하는 Step 1 패키지입니다. 치환·가명 생성은 Step 2 범위입니다.

## 구조

- `src/pii_detector/detector.py`: 개인정보 탐지, 결과 퓨전, 경계 검증 로직
- `loader.py`, `backends.py`, `factory.py`: 고정 HF revision 로딩, A.X 추론, Veil FP32 ONNX 및 공식 Veil 디코더, 통합 생성기
- `tests/test_detector_boundaries.py`, `test_detector_runtime.py`: 경계 처리와 런타임 안전성 회귀 테스트
- `tests/test_detector_reference_parity.py`: 저장된 기준 구현과 합성 입력 결과 비교
- `evaluation/run_compare.py`: Holdout 채점기
- `src/pii_detector/model_manifest.json`: 고정된 모델 저장소와 revision, 패키지 리소스로 읽음

A.X가 person, phone, email, RRN, account, card를 담당하고 Veil이 address를 담당합니다. 출력 offset은 Python 문자열 기준 반열린 구간 `[start, end)`입니다.

## 설치 및 실행

지원 Python 버전은 `>=3.10,<3.13`입니다. 프로젝트 루트의 `.env` 또는 환경변수 `HF_TOKEN`에 Hugging Face 읽기 토큰을 설정합니다. 토큰 값은 저장소에 커밋하지 마세요.

```bash
cd step1_pii_detector
python -m pip install -e .
cp .env.example .env  # 이미 프로젝트 루트 .env를 사용하는 경우 생략
```

프로젝트 루트 `.env`는 factory가 자동으로 읽습니다. 첫 초기화 때 두 고정 revision 모델을 다운로드하고 Veil FP32 ONNX를 준비합니다. 같은 detector 인스턴스는 이후 요청에서 모델을 다시 로드하지 않습니다.

```python
from pii_detector import create_detector

detector = create_detector()
try:
    result = detector.detect("고객명: 홍길동")
finally:
    detector.close()
```

결과의 `status`는 `READY_FOR_PSEUDONYMIZATION`, `REVIEW_REQUIRED`, `BLOCKED`입니다. READY는 탐지기 처리 상태일 뿐 누락 PII가 없다는 보증이나 외부 전송 승인이 아닙니다.

## 검증

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_detector_reference_parity -v
```

합성 패리티 테스트는 보관된 기준 구현과 패키지 결과의 상태, span, 이슈가 같은지 확인하며 HF 토큰이나 모델 다운로드가 필요하지 않습니다. 기준 노트북이 없으면 해당 비교만 건너뜁니다. 이 검증은 실제 모델 추론 비교를 의미하지 않습니다. 별도 벤치마크는 다음처럼 실행합니다.

```bash
python evaluation/run_compare.py --gold test.jsonl --pred model=predictions.jsonl
```

벤치마크는 strict span 일치, partial 중첩, mask recall을 보고합니다. 기준 구현 비교는 기능/안전 회귀이며 blind holdout 정확도 인증은 아닙니다.
