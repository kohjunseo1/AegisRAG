# Unified V1 Follow-up — E0~E5

> **최종: E0~E5 코드/로컬 검증 완료, E5에서 종료.** 실제 A.X/Veil/KLUE 합성 추론 및 pinned KoE5 + 기존 187행 읽기 전용 검색 PASS. 실제 HCX 호출 0회, DB 쓰기/재적재 0회, push/PR/main 변경 없음. 최종 테스트 **300개 실행: 299 PASS / 1 SKIP / FAIL 0**. 실모델 가용성과 전체 원본 런타임 parity/운영 승인은 별개다.

| 단계 | 최종 상태 | 미완 검증 경계 |
|---|---|---|
| E0 | DONE — 기존 회귀/합성 parity | 실제 기존 88질의 재평가는 미수행, 기존 결과 보존 |
| E1 | DONE — 상주 worker/3모델 smoke/30회 warm | KoELECTRA MISSING; KLUE 원본 model_predict 전체 parity 및 AX/Veil 개별 cold/RSS 귀속 BLOCKED |
| E2 | DONE — Fusion 상태 분리/평가 도구/실제 후보의 합성 ablation | 실문서/Blind 일반화 성능 미측정 |
| E3 | DONE — 이름 후보 vs 동일인 확정/모호성 테스트 | 실제 identity 품질 미측정 |
| E4 | DONE — 운영 DB readonly/KoE5/Top-5 parity | 격리 Postgres DDL integration DB_REAL_NOT_RUN; DB embedding revision 증명 metadata 없음 |
| E5 | DONE — 기존 HCX client LiveTransport + Guard/Mock | 실제 유료 호출 NOT_RUN_BY_POLICY |


2026-11-19 (Asia/Seoul). 작업 worktree: AegisRAG-unified-v1 / feature/unified-v1. 기준 HEAD 39581cb. 원본 V6/main/HMAC/운영 DB 쓰기/다른 worktree/팀원 원본 변경 금지. 실제 HCX 유료 호출·GitHub push 금지. 결과는 이 파일에만 누적한다.

## E0 — DONE (synthetic); 실제 88질의 parity는 아직 미측정

- 기존 Step1 31개(1 skip), Step2 183개, 루트 29개, Unified 41개 모두 OK.
- `test_followup_e0.py` 추가: 동일 합성 raw 후보에 대해 원본 PIIDetector/Gate/문서 준비/HMAC과 신규 own2의 Mask·Gate·가명 출력 동등성 PASS (4 fixtures / 1 test).
- 명령: `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -p test_followup_e0.py -v`.
- `$PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python`. 팀원 참조 SHA 242543bf 재확인(읽기 전용). main/기존 실행 코드 수정 없음.
- 별도 임시 venv `/tmp/aegis-unified-followup-model-env`에만 transformers 4.55.4 / tokenizers 0.21.4 / huggingface-hub 0.36.2 설치. 기존 Conda는 변경하지 않음. 현재 기존 Conda의 hub는 2.4.0이며 이 버전도 고정 lock과 다름. 실모델 가용성은 E1에서 별도 기록.

## E1 — 구현/Mock DONE; 실제 로컬 계측 진행

- quality3/ax_klue/veil_klue 추가. REAL_READY는 실제 검증된 source 상태만으로 계산하고 KoELECTRA 의존 조합은 MISSING.
- PersistentPredictor/상주 NDJSON worker, 요청별 ID/fingerprint/config 검증, 비밀 환경변수 제외, 요청 직렬화/timeout/영구 fault 및 프로세스 정리 구현. 선택된 own source만 추론하며 요청별 원문 캐시는 종료 즉시 해제.
- SnapshotDetector/SnapshotGateAdapter가 동일 raw evidence에 원본 V6 정책을 적용. Gate에서 재추론하지 않으며 team2는 own 모델을 몰래 호출하지 않고 신호 부족으로 BLOCKED.
- 임시 격리 환경에서 pinned A.X/Veil 실제 로딩·합성 추론 PASS. KLUE는 로딩 후 tuple 출력(`return_dict=false`) 때문에 기존 `.logits` 접근 실패: tuple/ModelOutput 양쪽을 지원하도록 수정. 원본 팀원 코드의 `.logits` 경로는 이 환경에서 별도 비교해야 함.
- E1 Mock 테스트 3 PASS: readiness, 중복 추론 금지/비선택 source, 상주 프로세스 재사용/동시 요청/timeout/fault.
- 변경: contracts.py, adapters.py, worker.py, runtime.py, replay.py, local_validation.py. 실제 30회 warm 계측/3모델 합성 결과는 최종 절에 기록.

## E2 — DONE (synthetic); 실제 품질 지표 미측정

- evidence_v1 호환 경로 유지, evidence_v2 추가: CONFIRMED_HMAC / LOCAL_MASK_ONLY_PENDING_APPROVAL / REVIEW / BLOCKED 구분. 완전한 새 후보를 마스크만 하고 로컬 준비 가능하게 하되 승인 상태는 NOT_APPROVED.
- 번호/이메일의 정확한 형식·정규화 검증 시에만 Typed HMAC 추가. 라벨/불확실 HMAC 경계 충돌은 REVIEW, Gate 안전 신호 없는 프로파일은 BLOCKED. baseline 및 모든 신규 후보 보호 합집합 유지.
- 동일 raw 후보의 strict F1·FP/FN·문자 보호율·완전 미탐·HMAC 확정률·Gate 상태 비교 도구 evaluation.py 추가. 합성/실데이터 역할을 명시하고 본문/Gold/span을 출력하지 않음.
- E2 신규 3 PASS. 기존 포함 Unified 누적 48 PASS. synthetic.py의 기존 v1 demo는 기존 정책을 명시하여 버전 혼합을 막음(기대값 완화 없음).
- 명령: `PYTHONPATH=step2_privacy_rag/app $PY -m unittest discover -s tests/unified_v1 -v`. 신규 LocalPipeline은 raw 재사용 + evidence_v2가 기본이며 구 경로를 옵션으로 보존.

## E3 — DONE (synthetic)

- 기존 name lookup / label-scoped attribute HMAC / 별도 Entity key / Mention ID를 재사용했다. 이름만 있는 query는 lookup_only 후보 검색을 허용하며 동일인 exact로 승격하지 않는다.
- 알려진 서로 다른 Entity 후보가 실제 답변 context에 함께 남으면 CLARIFY. 소유 속성이 충돌/불명확한 query는 기존처럼 차단. Entity IDs와 name lookup metadata를 인덱스/질의/검색 evidence에 전달.
- 기존 공유 번호/변경 연락처/누락 속성/문서 내 동명이인 resolver 테스트를 유지하고, 이름만의 단일 후보 허용·복수 Entity 모호성 검사 2개 추가. 누적 Unified 50 PASS.
- 변경: contracts.py, retrieval.py, pipeline.py, generation.py, test_followup_e3.py. 기존 HMAC/정규화 변경 없음.

### E1 실제 로컬 실행 결과

- 격리 Python 3.11 / torch 2.2.2 + Transformers 4.55.4에서 **A.X/Veil/KLUE LOCAL_MODEL_PASS**, 합성 5문장 quality3 raw 추론 OK(4건 CONFIRMED_HMAC, 1건 REVIEW). 결과를 READY로 강제 승격하지 않음.
- 같은 합성 문장, warm-up 후 30회, stdio 왕복 포함: own2 묶음 p50 109.63ms / p95 154.05ms / cold 17,952.98ms / child peak RSS 2,209.04MiB. KLUE p50 29.83ms / p95 35.01ms / cold 9,176.02ms / child peak RSS 490.45MiB. own2는 원본 동시 로더의 묶음 계측이며 개별 모델 cold/RSS로 해석하지 않음. 전체 시스템 동시 RAM/실데이터 품질은 미측정.
- own2/quality3/ax_klue/veil_klue/drop_koelectra는 모델 가용성 REAL_READY. 이는 **모델 추론 가용성**이며 Gate/적재/운영 승인이 아님. ax_klue/veil_klue는 기존 Gate 신호 부족 시 계속 BLOCKED. team2/quality4/나머지 drop은 KoELECTRA MISSING.
- 상주 worker 최초 실패는 실행기에서 venv Python symlink를 resolve하여 기존 Conda interpreter로 실행한 결함. absolute 경로를 보존하도록 수정 후 실제 로컬 추론/30회 계측 성공. 기존 환경 변경 없음.

## E4 — 구현/Mock DONE; 실제 DB metadata PASS, KoE5 검색 검증 진행

- ExistingIndexReader는 연결 옵션 default_transaction_read_only=on + REPEATABLE READ READ ONLY + SHOW 검증을 사용. 187행/유일 doc_id/key_tag/컬럼 type/vector(1024)/전체 row fingerprint를 검사하고 변경 시 차단.
- **운영 DB 읽기 전용 metadata 실측 PASS**: 정확히 187행, 기존 HMAC key tag 일치, 스키마 일치. Token GIN 없음: 기록만 하고 DDL/재적재하지 않음. DB에 embedding revision 증명 metadata는 없어 기존 V6 manifest 선언과 구분함.
- ALL/ANY/Region/Dense SQL 후보 union과 기존 ranking 함수 공통화. 기존 187행 Dense 전체를 사용하여 V6 기준 의미 보존. legacy 검색 결과는 LEGACY_READ_ONLY로 표시하여 외부 전송 승인과 혼동하지 않음. Entity metadata는 별도 in-memory sidecar로 전달 가능.
- 격리 migration에 실제 vector(1024), token/region GIN SQL 및 명시적 별도 DB 확인 경로 작성. 운영 DB와 동일한 DB는 실행 전 차단. 이번에 격리 DDL은 실행하지 않음(DB_REAL_NOT_RUN).
- 신규 E4 3 PASS, 누적 Unified 53 PASS. KoE5 smoke 최초 ModuleNotFoundError는 환경 문제로 분석 중; 최종 결과는 아래 추가 기록.

### E1 원본 decoder 비교 및 긴 입력

- 실제 KLUE 로짓과 원본 `_tags_to_spans` 정의를 읽기 전용으로 실행해 신규 raw decoder와 합성 5문장 일치 확인. 실제 tokenizer overflow 긴 한 줄 2개 window, 마지막 문자 offset coverage PASS.
- **원본 `model_predict` 전체 런타임 parity는 BLOCKED**: 전달 config의 return_dict=false 때문에 원본 `.logits` 접근이 AttributeError. 신규 어댑터의 tuple 지원으로 로컬 추론은 가능하지만 이를 원본 런타임 동등성 전체 PASS로 포장하지 않음.

### E4 실제 KoE5/187행 읽기 전용 검색 — PASS

- 모델 PoC venv에서는 sentence-transformers 의존성 ModuleNotFoundError. 기존 별도 `aegisrag-retrieval` 환경(torch 2.2.2 / transformers 4.55.4 / sentence-transformers 3.4.1)을 **변경 없이** 사용하여 pinned KoE5의 실제 로컬 embedding + 187행 SQL 검색 PASS.
- 동일한 고정 합성 질의/동일 vector로 own_v6/team_v3/hybrid_v1 검색. 실제 Top-5(세 프로파일 동일): `H0148, H0151, H0175, H0183, H0112`. 원본 V6 retrieve의 Top-5와 own_v6 일치 PASS. 검색 전/후 전체 row fingerprint 불변 PASS.
- query 전처리는 알려진 합성 fixture, KoE5/DB는 실제 실행이다. Gold가 없는 smoke이므로 Hit/F1/MRR 품질 성능을 측정했다고 보고하지 않음. 실제 기존 88질의 재평가·새 Blind 미실행. 기존 결과 변경/재적재 0회.
- 실행: `PYTHONPATH=step2_privacy_rag/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python -m unified_v1 --db-readonly-smoke --env-file /Users/gojunseo/AegisRAG/.env`와 동일한 run_db_smoke 경로. credential 값은 메모리에서만 참조.

## E5 — DONE (코드/Mock); 실제 HCX NOT_RUN_BY_POLICY

- 기존 hcx.call_hcx를 호출하는 LiveTransport와 GuardedGenerator 추가. 기존 model/schema/타임아웃/HTTP 예외/인용 검증을 그대로 재사용. 기존 .env 파일의 부모 경로만 전달하고 키를 복사/로그/저장하지 않음.
- 기본 OFF. 향후 실행에는 명시적 LivePermit, 정확한 합성 context fingerprint, synthetic corpus, 기존 V6 외부 전송 opt-in 환경변수, 최대 호출 수, 사전 정의된 보수적 비용 예약 상한이 모두 필요. 공급자 실제 요금 계산기는 아니며 실패 시에도 예약/호출 한도를 소모하고 자동 재시도하지 않음.
- 비공개 corpus/legacy 운영 검색 결과의 외부 전송 차단. 동일 ContextBuilder/독립 Outbound 검사 → 기존 HCX 함수 → schema/citation → 독립 Output Recheck 연결. context별 Region 상태를 payload에 묶어 공유 transport의 요청 간 상태 혼선을 방지.
- 신규 3 PASS: 기본 OFF 무호출, 기존 client 위임, context/비용/호출 한도, private corpus 차단, 응답의 임의 토큰/잔여 PII 차단, timeout 재시도 금지. 모든 client 호출은 Mock, 실제 네트워크는 차단했다. 누적 Unified 56 PASS(최종 회귀 결과는 아래).
- 변경: generation.py, __main__.py, test_followup_e5.py. Config.api_live 기본 OFF/기존 MockGenerator 계약 유지. 실제 API 요청 0회.

## 최종 추가 계측·Ablation (실모델이지만 합성 5문장만)

같은 고정 합성 입력, 같은 Gold, 같은 요청별 원시 후보 snapshot을 재사용했다. below 값은 **실제 모델 후보를 사용한 소규모 합성 계약 검증**이며 실제 187/88 또는 Blind 성능이 아니다. BLOCKED 프로파일의 보호/Strict 수치는 진단용이고 승인된 출력 성능이 아니다.

| profile | Strict micro F1 | Strict FP/FN | Gold 문자 보호율 | 완전 미탐 | READY/REVIEW/BLOCKED |
|---|---:|---:|---:|---:|---:|
| own2 | 0.8571 | 1 / 1 | 1.0000 | 0 | 4 / 1 / 0 |
| quality3 | 0.8571 | 1 / 1 | 1.0000 | 0 | 4 / 1 / 0 |
| ax_klue | 0.2500 | 0 / 6 | 1.0000 | 0 | 0 / 0 / 5 |
| veil_klue | 0.2500 | 0 / 6 | 0.9608 | 0 | 0 / 0 / 5 |
| drop_koelectra | 0.8571 | 1 / 1 | 1.0000 | 0 | 4 / 1 / 0 |

- ax_klue/veil_klue의 BLOCKED는 누락된 기존 Gate 안전 신호 때문이며, 부족한 보호/확정 상태를 READY로 우회하지 않는다. quality3가 항상 품질을 개선한다는 결론을 내리지 않는다.
- 별도 선택 source 30회 warm(상주 own2 worker, 같은 합성 문장, stdio 포함): AX p50 **64.00ms** / p95 **89.60ms**, Veil p50 **20.07ms** / p95 **26.19ms**. 원본 공동 로더 때문에 **개별 cold load/RSS 귀속은 BLOCKED**이며 앞의 own2 공동 cold/RSS를 각 모델에 나눠 보고하지 않는다. KLUE 개별 값은 E1 표기와 같다.
- 모델 프로파일 선택 시 worker가 요청된 AX 또는 Veil만 추론하는 별도 테스트 추가(기존 공동 로더가 둘 다 로드할 수 있는 사실과 구분).
- 실제 F1/보호율 측정은 이 고정 합성 5문장에 한정. 실제 검색 품질(Hit/MRR/Recall/confusable), 실제 88개 질의 전체 분모 결과는 미측정이며 API/scorer만 준비했다. 실제 Top-5 smoke는 Gold가 없어 점수로 보고하지 않는다.
- HMAC 확정률은 UNIQUE_TYPED_AND_UNRESOLVED_CANDIDATES를 분모로 명시하며 Gold 개수로 나눠 오탐 상황에서 1을 초과하는 비율을 만들지 않는다. pipeline은 최종 PreparedInput에도 요청별 raw evidence를 보존하되 원문은 담지 않는다.

## 최종 검증·재실행 명령

| 검사 | 결과 |
|---|---|
| 기존 Step1 | 31개: 30 PASS, 원본 release notebook 부재 1 SKIP |
| 기존 Step2 | 183 PASS |
| 기존 루트 | 29 PASS |
| Unified 전체 | 57 PASS (기존 41 + followup 16) |
| 총계 | **300 실행 / 299 PASS / 1 SKIP / 최종 FAIL 0** |
| synthetic quality3 Mock E2E / CLI help | PASS |
| 운영 DB 읽기 전용/실 KoE5/원본 Top-5 parity | PASS, 187행, 전후 row fingerprint 불변 |
| 신규/변경 소스 AST, diff whitespace, 실제 .env 비밀값의 소스 유입 검사 | PASS(원문/키/DSN 출력 없음) |
| V6/requirements.lock/HMAC/Region/main/팀원 참조 변경 | 없음 |
| 실 HCX/LLM/외부 임베딩 API / 운영 DB 쓰기·재적재 / GitHub push·PR | 0회 |

```bash
cd /Users/gojunseo/AegisRAG-unified-v1
AEGIS_FOLLOWUP_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python
PYTHONPATH=step1_pii_detector:step1_pii_detector/src "$AEGIS_FOLLOWUP_PY" -m unittest discover -s step1_pii_detector/tests -v
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FOLLOWUP_PY" -m unittest discover -s step2_privacy_rag/tests -v
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FOLLOWUP_PY" -m unittest discover -s tests -v
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FOLLOWUP_PY" -m unittest discover -s tests/unified_v1 -v
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FOLLOWUP_PY" -m unified_v1 --profile quality3 --synthetic-e2e
# 실제 로컬 모델만: paid API 없음. 임시 venv는 OS 정리 후 재생성해야 할 수 있음.
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FOLLOWUP_PY" -m unified_v1 --local-smoke --runtime-python /tmp/aegis-unified-followup-model-env/bin/python
# 기존 별도 검색 환경을 수정 없이 사용: 운영 DB 읽기 전용, 외부 API 없음.
PYTHONPATH=step2_privacy_rag/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python -m unified_v1 --db-readonly-smoke --env-file /Users/gojunseo/AegisRAG/.env
```

해결한 실패: KLUE tuple 출력 호환, venv symlink resolve 문제, v1/v2 demo 정책 버전 혼합, 평가 API 기본값 편집 중 NameError(수정 후 전체 Unified 재실행 PASS). KoE5 smoke의 환경 의존성 실패는 기존 정상 retrieval 환경 선택으로 해결했다. 테스트 기대값을 완화하지 않았다.

**다음 필요한 입력:** KoELECTRA ft5/발신자 checksum·원본 합성 예상 출력, KLUE 원본 전체 runtime을 재현할 export/config 또는 팀원 호환 환경, 원본 Gate와 동등한 신호가 없는 ablation의 승인 계약, 격리 Postgres가 필요한 경우 폐기 가능한 별도 DB. 실제 HCX 호출은 이번에 수행하지 않았으며 향후에도 합성 데이터·명시적 opt-in·호출/비용 예약 한도가 필요하다. 이번 변경은 미커밋 로컬 상태로 남기고 **E5에서 중단**한다.

## 후속 브랜치 게시 요청

E5 종료 후 사용자가 feature/unified-v1 브랜치의 커밋 및 GitHub push를 명시적으로 요청했다. 위 미커밋/미push 기록은 E5 종료 시점이며, 후속 게시 범위는 이번 구현·테스트·단일 결과 문서와 기준 명세다. main 병합/PR 생성/유료 호출/운영 DB 쓰기는 포함하지 않는다.
