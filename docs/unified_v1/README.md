# Unified V1 로컬 실행

통합 코드는 `step2_privacy_rag/app/unified_v1`에만 추가했다. 기존 V6 CLI·Gate·HMAC·requirements.lock은 유지한다. 신규 패키지는 import 시 모델/DB/네트워크를 실행하지 않는다.

통합 worktree에서 기존 Python을 명시해 실행한다. 아래 명령은 비공개 데이터·키·모델 없이 동작하며, 기존 환경 설치/업그레이드가 필요 없다.

```bash
cd /Users/gojunseo/AegisRAG-unified-v1
AEGIS_UNIFIED_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python
PYTHONPATH=step2_privacy_rag/app "$AEGIS_UNIFIED_PY" -m unittest discover -s tests/unified_v1 -v
PYTHONPATH=step2_privacy_rag/app "$AEGIS_UNIFIED_PY" -m unified_v1 --profile quality4 --synthetic-e2e
```

`--synthetic-e2e`는 네트워크를 차단하고, 합성 원문 → raw mock 후보 → 원본 Gate → Fusion → 원본 HMAC → 소유 관계/보수적 linking → 독립 재검사/명시적 실험 승인 → 인메모리 검색 3종 → HCX schema mock → 출력 검사를 수행한다. 출력 JSON은 본문·질의·가명 매핑 없이 상태, 문서 ID, **합성** 검색 지표, 실험 매니페스트만 포함한다. 이 지표는 실모델 성능이 아니다. 실행시간/p95/RSS는 NOT_MEASURED다.

```bash
PYTHONPATH=step2_privacy_rag/app "$AEGIS_UNIFIED_PY" -m unified_v1 --profile quality4
```

모델을 주입하지 않은 기본 dry-run은 모든 모델을 UNAVAILABLE로 표시하고 종료코드 2로 BLOCKED를 반환한다. `--synthetic`는 빈 mock 후보의 계약 검사만, `--synthetic-e2e`는 전체 합성 흐름을 실행한다. 두 경우 모두 모델 상태에 mock임을 표시한다.

## 모듈 계약

| 모듈 | 역할 |
|---|---|
| contracts | Unicode codepoint 후보, 불변 탐지 증거, ProtectionPlan, 준비/승인/검색 타입, 검증된 설정 |
| adapters / worker | A.X/Veil/KLUE/KoELECTRA raw 어댑터, 오프라인 HF overflow decoder, 로컬 stdio IPC, 엄격한 휘발성 replay |
| protection | 기존 Gate 어댑터, 번호/주소 Sentinel, 문맥/반복 이름 후보, 마스크 합집합, 로컬 가명처리 |
| identity | 원본 96-bit HMAC 호출, 별도 이름 lookup/Entity 키, 필드 소유 관계, 보수적 UNRESOLVED resolver |
| pipeline | 명시적 document/query 전처리와 unresolved 질의 차단 연결 |
| retrieval | 승인된 가명 문서의 insert-once 인메모리 인덱스, 세 검색 알고리즘, 기존 공통 scorer |
| database | 고유 테스트 스키마 migration 계획. 실제 실행은 BLOCKED |
| generation | 기존 HCX-007 schema/guard 재사용, 독립 NER/패턴 재검사, Context, MockTransport, 인용 검증 |
| experiment / synthetic | PII-free 실험 매니페스트 및 재현 가능한 합성 실행 |

실제 모델을 붙일 때는 원본 pinned predictor를 각 raw 어댑터에 주입한다. 팀원 로컬 HF 모델은 `LocalHFPredictor`를 별도 호환 환경에서 실행한다. 긴 줄은 tokenizer overflow window로 처리하며, unknown label은 오류이고 제외 라벨은 명시적 allowlist다. 원본 팀원 후처리와 새 raw decoder의 동등성은 별도 검증해야 한다.

로컬 IPC는 `SubprocessPredictor([absolute_python, absolute_worker, absolute_private_manifest], config_hash(manifest))` 형태로 연결한다. worker 경로는 `step2_privacy_rag/app/unified_v1/worker.py`다. private manifest에는 `source_id`, `source_revision`, `model_dir`, `trusted_root`, `hashes`(4개 필수 파일), `ignore_labels`, `max_length`, `stride`를 명시한다. 예측 조건 전체와 artifact hash가 config hash에 묶인다. manifest는 비공개 로컬 경로에 보존한다. worker는 stdin request ID/문서 전체 fingerprint/config hash를 검증하고 stdout에 raw span만 반환하며 네트워크 연결을 차단한다. timeout이나 응답 불일치는 fallback 없이 차단한다. 이 실행 경로의 실모델 parity는 아직 BLOCKED다.

`PreparationResult`의 LOCAL_PREPARED는 전송/적재 승인이 아니다. `approve_experiment`의 독립 checker와 명시적 실험 corpus 승인을 거쳐야 한다. 이는 로컬 실험용 호출자 계약이며 ACL이나 암호학적 권한 증명이 아니다. 인물 질의는 `query_from_mentions`를 통해 구성하여 UNRESOLVED를 검색/생성으로 우회하지 않는다. 원문/Gold/키는 인덱스에 넣지 않는다.

Mask-only 자동 적재는 OFF이며, 별도 `allow_mask_only=True`로 승인된 실험만 가능하다. 청크 인덱스, RRF, adaptive, 실 DB 실행, live HCX, P6는 이번 범위에서 활성화하지 않았다. 인용 ID/제공된 근거 범위 검증은 사실성 검증과 별개다.
