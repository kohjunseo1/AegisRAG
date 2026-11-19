# Unified V1 Final E2E — F0~F5

> **최종: F0~F5 구현·가능한 실측 완료, F5에서 종료.** 4모델 실추론/KoELECTRA raw decoder/과거 187문서 parity/88질의 실제 KoE5·DB 검색 PASS. 신규 합성 인메모리 실제 KoE5 + Mock E2E PASS. 전체 88질의×3모델조합×3Retriever 로컬 E2E는 실행 완료했으나 독립 Context 재검사 차단이 많아 **운영 승격/실제 답변 품질 검증 완료는 아님**. 발표용 비교 기준 추천은 **own2 + own_v6 유지**이며 기존 서비스 기본값을 변경하지 않았다. 실제 HCX/외부 개인정보 전송/운영 DB 쓰기/push는 0회.

| 단계 | 최종 상태 | 한계 |
|---|---|---|
| F0 | PASS — 4개 실제 모델 및 KoELECTRA raw decoder/오프셋 | 팀원 merge/trim/ALIEN 정책은 의도적 차이; KLUE 원본 전체 runtime의 tuple 설정 차이는 이전과 같음 |
| F1 | PASS — 187문서 실제 비교/상태·HMAC parity/마스크 단조성 | seen_dev 지표, 새 Blind/전체 승인 출력 품질 아님 |
| F2 | PASS — signed lookup sidecar/가짜 동일인 병합 방지 | 운영 Entity 소유 증거 없음 → 100% UNRESOLVED 유지 |
| F3 | PASS — 기존 DB 187행/88질의/85개 Top-5 parity | 격리 PostgreSQL integration BLOCKED, 기존 DB에는 신규 DDL 없음 |
| F4 | PASS — Mock/호출 가드/신규 합성 실모델 E2E | 실제 HCX NOT_RUN_BY_POLICY; 기존 corpus Context 재검사 다수 BLOCKED |
| F5 | PASS — 단일 재현 CLI/실측 요약/비교 | 새 UI/운영 전환 미수행, 주장 가능한 범위는 아래 명시 |


2026-11-19, Asia/Seoul. 기준 HEAD 2d0bb5d / feature/unified-v1. 기존 V6/main/96-bit HMAC/운영 DB 변경, GitHub push/PR, 실제 유료 HCX·외부 개인정보 전송 금지. 결과 문서는 이 파일 하나만 사용한다. 실측 요약 JSON은 본문/원문 span 없이 별도 reports 또는 로컬 경로에 저장한다.

## F0 — 실제 4모델 합성 PASS / 원본 raw decoder PASS

- KoELECTRA는 이미 Git 외부 `~/private-models/aegisrag/koelectra_ft5`에 존재. 재압축 해제/다운로드/대체 없이 설계서의 4파일 SHA와 모두 일치 확인.
- Electra/59 outputs/15개 extra-sensitive 유형/6개 명시적 non-sensitive allowlist/7종 core 매핑 구현. ALIEN_ID를 RRN으로 HMAC 승격하지 않는다. 민감 extra는 원시 출처/원본 유형을 보존하고 Mask+REVIEW로 처리한다.
- 상주 worker의 4모델 합성 5문장 추론 PASS. own2/quality3/quality4/team2/drop/ax_klue/veil_klue 모델 가용성 REAL_READY(적재/Gate 승인 아님).
- KoELECTRA 원본 hf_detector.decode와 같은 실제 로짓/tag/Unicode offset에서 5/5 raw span 일치 PASS. 256/512 overflow 각각 4/2 window, 원문 마지막 offset 보존 PASS. merge_adjacent/to_ours/_trim 미적용 및 extra-sensitive 정책은 의도적 차이로 분리했으며 원본 최종 정책 동등성으로 주장하지 않음.
- 기존 격리 model venv 재사용(Python 3.11.13, torch 2.2.2, transformers 4.55.4, tokenizers 0.21.4, hub 0.36.2). 기존 Conda 업그레이드 없음.
- 동일 합성 입력 warm 30회(IPC 포함): own2 묶음 p50 116.76ms/p95 167.02ms/cold 27,583.18ms/child peak RSS 1,940.45MiB; KLUE 62.47/170.52ms/cold 11,768.27ms/RSS 491.66MiB; KoELECTRA 38.43/120.83ms/cold 12,626.34ms/RSS 365.63MiB. 다른 로컬 작업과 동시 실행된 조건의 수치이며 own2 개별 cold/RSS로 분리하지 않음. 동시 child RSS 샘플 최대 2,031.07MiB(연속 측정 peak 보장 아님).
- F0 신규 3 tests PASS. 기존 포함 Unified 60 PASS.

## F1 — 코드/합성 회귀 PASS (최종 187문서 실측은 아래 별도 기록)

- NO_PII_CANDIDATES 상태를 추가하여 개체 없는 문서를 CONFIRMED_HMAC으로 집계하지 않는다. 기존 HMAC 개체 없는 mask-only도 별도 상태로 표시.
- REVIEW/BLOCKED에서도 원본 V6 detector의 보호 마스크가 diagnostics에서 사라지지 않도록 SnapshotGateAdapter를 보완. 신규 후보/extra/경계 충돌은 기존처럼 보호·보류하고 HMAC 승격하지 않는다.
- F1 신규 2 tests PASS, 누적 Unified 62 PASS. 187문서/실제 Gold 912개(이미 본 개발·회귀 자료)를 raw NER와 Gate 포함 지표로 구분하여 실행했다. 원본 실제 V6 prepare와 adapter의 상태/HMAC 및 마스크 단조성도 비교했으며 최종 결과는 아래 표에 기록했다.

## F2 — 코드/합성 PASS

- 운영 DB를 변경하지 않고 signed local Sidecar를 연결. doc별 masked fingerprint/index fingerprint/key tag/signature를 검증하며 HMAC 이름은 후보 lookup으로만 사용한다.
- 원문 소유 관계와 일치하는 가명 본문 증거가 없으면 Entity를 생성하지 않는다. shared/changed 연락처/역할/이름만으로 동일인 확정 금지. sidecar는 메타데이터 승인이지 외부 전송 승인 아님.
- lookup-only 후보가 Dense에 밀려 hybrid Top-5에서 사라지던 정렬 결함을 보완하고 exact identity로 승격되지 않음을 검증.
- F2 신규 2 tests PASS. 실제 187행 Sidecar metadata coverage/UNRESOLVED 비율은 F3 결과와 함께 기록.

## F3 — 실제 187행/88질의 검색 PASS

- 기존 prepared query artifact를 읽기 전용으로 사용(READY 85 / DEFERRED 3). 모든 Retriever에 **동일한 기존 가명 질의·동일 KoE5 vector·동일 DB**를 적용했다. 저장 인덱스 정책 `legacy_v6`, 질의 정책 `legacy_v6_query_frozen_artifact`이며 evidence_v2 질의/새 인덱스 결과와 혼동하지 않는다.
- 원본 V6 `order_results(...topic_boost=False)`와 own_v6의 실제 Top-5 **85/85 일치**. 검사는 bool 표시가 아니라 불일치 시 LEGACY_TOP5_PARITY_FAILED 오류로 실패한다. Region 다중 키의 개수를 우선하는 원본 정렬을 보존하도록 보완했다.
- 기존 운영 DB 187행, key tag/vector(1024)/schema 확인, 평가 전후 전체 row fingerprint 동일. Token GIN은 없음: 인덱스를 생성하거나 재적재하지 않았다. embedding revision은 기존 manifest 선언이며 DB 자체 provenance attestation은 없음을 별도 표기.
- 로컬 Sidecar 실제 coverage: 187문서 중 이름 후보 문서 173, doc별 unique-name 총 442, Entity 확정 문서 0, UNRESOLVED 442/442=100%. 이름 HMAC만으로 소유 관계를 만들어내지 않은 결과다. sidecar는 Git 외부 `.cache/aegisrag/unified_v1/private/`에 0600으로 저장·서명 검증하여 reader에 연결했다.
- 격리 PostgreSQL은 별도 폐기 가능한 DB를 확인하지 못했으므로 **BLOCKED / DB_REAL_NOT_RUN**. 기존 운영 DB에 격리 스키마조차 만들지 않았다. 신규 텍스트 E2E는 별도의 실제 KoE5 인메모리 인덱스로 실행하며 SQL 통합 결과로 부르지 않는다.

| Retriever | Hit@1 (READY 85) | Hit@5 (READY 85) | 전체 88 Hit@1 / Hit@5 | MRR(85) | Recall@5 | Confusable@1 / @5 |
|---|---:|---:|---:|---:|---:|---:|
| own_v6 | 77/85 (0.9059) | 83/85 (0.9765) | 0.8750 / 0.9432 | 0.9353 | 0.8848 | 5/85 / 27/85 |
| team_v3 | 77/85 (0.9059) | 83/85 (0.9765) | 0.8750 / 0.9432 | 0.9353 | 0.8848 | 5/85 / 27/85 |
| hybrid_v1 | 77/85 (0.9059) | 83/85 (0.9765) | 0.8750 / 0.9432 | 0.9353 | 0.8848 | 5/85 / 27/85 |

neutral 문서는 scoring 시에만 제외한다. 실제 사용자 Top-5는 제외 없이 JSON에 QID/문서 ID로 기록했다. 위 자료는 이미 본 seen_dev이며 새로운 Blind 점수가 아니다. 이번 비교에서 Retriever 개선은 관측되지 않았다.

## F1 과거 187문서 실측 및 원본 V6 parity — PASS

- 원본 실제 V6 prepare를 원본 pinned predictor로 실행한 결과와 SnapshotGateAdapter 비교: 187문서 상태 불일치 0, 양쪽 LOCAL_PREPARED인 177문서 HMAC 최종 출력 fingerprint 불일치 0, 마스크 회귀 0. 신규 Fusion 정책의 추가 REVIEW는 아래 별도 집계했다.
- 같은 원문 187개/Gold 912개/정규화, 4모델 원시 후보를 한 번 얻고 프로파일별로 재생했다. `Raw F1`은 core 7종 합집합 NER, `Pipeline strict F1`은 REVIEW/BLOCKED를 포함한 strict 후보 평가다. latter를 승인 출력/전체 E2E 성능으로 해석하면 안 된다. 문자 보호율도 모든 상태의 ProtectionPlan diagnostics를 기준으로 한다.

| Profile | Raw micro F1 | Pipeline strict micro F1 | Gold 문자 보호율 | 완전 미탐 | READY / REVIEW / BLOCKED | HMAC 확정률 |
|---|---:|---:|---:|---:|---:|---:|
| own2 | 0.9521 | 0.9610 | 1.0000 | 0 | 152 / 35 / 0 | 0.7986 |
| quality3 | 0.8791 | 0.9610 | 1.0000 | 0 | 126 / 61 / 0 | 0.6875 |
| quality4 | 0.8599 | 0.9610 | 1.0000 | 0 | 119 / 68 / 0 | 0.6558 |
| team2 | 0.8845 | 0.2188* | 0.9974* | 0* | 0 / 0 / 187 | 0.0934* |
| drop_ax | 0.8669 | 0.2100* | 0.9974* | 0* | 0 / 0 / 187 | 0.0868* |
| drop_veil | 0.8757 | 0.2188* | 0.9974* | 0* | 0 / 0 / 187 | 0.0917* |
| drop_klue | 0.9273 | 0.9610 | 1.0000 | 0 | 143 / 44 / 0 | 0.7528 |
| drop_koelectra | 0.8791 | 0.9610 | 1.0000 | 0 | 126 / 61 / 0 | 0.6875 |

`*`는 V6 Gate에 필요한 source가 없는 **정책 BLOCKED의 진단 수치**다. 모델 성능 저하로 부르거나 사용할 수 있는 출력 점수로 비교하지 않는다. 실제 원시 모델은 모두 로딩/추론 가능하다. HMAC 확정률의 분모는 unique typed/unresolved 후보이며 probability 평균이 아니다.

현재 seen_dev에서 모델 추가가 이미 보호된 Gold 문자에 더한 이득은 없었고, 오탐/충돌과 검수 부담이 늘었다. 기존 V6가 로컬 준비한 177개와 비교하면 새 Sentinel/Fusion own2는 152개(-25), quality3는 126개(-51), quality4는 119개(-58)다. 이는 안전 정책/포함 후보가 달라진 결과이며 기존 V6 동작을 수정한 것이 아니다. 모든 단계의 raw P/R/F1·FP/FN·라벨별 문자 보호율·cold/corpus latency/RSS 상세는 PII-free summary JSON에 저장한다.

## F4 — 호출 경로/Mock PASS, 유료 HCX NOT_RUN_BY_POLICY

- 동일 ContextBuilder → 독립 실제 모델 Outbound 검사 → 기존 hcx.call_hcx 위임 → schema/citation → 독립 Output 재검사 경로 유지.
- LEGACY_READ_ONLY를 허용하는 것은 명시적 local_read_only **Mock** 경로뿐이다. GuardedGenerator의 Live 경로는 local-only context와 기존 legacy index를 무조건 거절한다. 메타데이터/검색 권한이 외부 전송 승인으로 바뀌지 않는다.
- 고정 합성 문서/질의만 받는 --synthetic-hcx-live 옵션 추가. 명시적 --opt-in, 환경 opt-in, 정확한 context hash, 최대 1~2회, 유한한 양수 예산/회당 보수적 비용 예약이 모두 있어야 실행 가능. 개인 문서/질의/Gold 경로 인자를 함께 주면 실행 전에 차단한다. **이번 작업에서 옵션을 opt-in하여 실행하지 않았다.**
- 실제 4모델 + 실제 KoE5의 신규 합성 인메모리 인덱스: 3문서 중 3문서 승인/색인, 검색→Mock HCX→Output recheck PASS(coverage 1.0, 인용 synthetic_education). 실제 SQL 신규 인덱스로 보고하지 않는다.
- 기존 DB로 첫 10개 seen_dev 질의의 quality4/hybrid 로컬 E2E: 3 Mock E2E PASS, 1 REVIEW, 6 독립 residual PII 검사 차단. 검사 기준을 완화하거나 무시하지 않았다. 전후 DB fingerprint 동일. 이는 생성 모델의 답변 정확도 30%라는 뜻이 아니다. Mock에서는 올바른 주체/claim faithfulness를 실측하지 않았다.
- 반복 RSS 측정의 ps subprocess가 tokenizer fork 경고를 유발한 문제는 libproc(/proc) 직접 조회로 수정. 환경 플래그를 요청마다 바꾸지 않는다. 전체 88질의 E2E 비교는 아래 최종 결과에 기록한다.

## F5 — 공통 로컬 실험 CLI 구현

- --check-models / --evaluate-step1 / --evaluate-retrieval / --e2e-local / --compare-e2e / --e2e-isolated-synthetic / --synthetic-hcx-live 제공. --report-dir에는 본문·원시 span 없는 요약 JSON만 생성한다. 원본 모델/질의/Gold는 로컬 입력 파일로만 읽고 데이터 역할은 seen_dev로 기록한다.
- V6 챗봇 기본 진입점은 연결/교체하지 않았다. 새 서비스/UI 대신 별도 unified_v1 CLI에서 입력 JSONL/프로파일/읽기 전용 검색을 실행한다. --compare-e2e는 같은 raw 후보·Gold를 사용한 3 Detector × 3 Retriever 비교이며 실제 모델 inference/KoE5/DB와 Mock 생성 경계를 표시한다.
- 격리 SQL schema 생성/삭제 API는 운영 DB와 다른 명시적 DB 및 UUID namespace, 생성 provenance comment를 검증해야 한다. 실제 운영 DB에서 DDL을 실행하지 않았고, 폐기 가능한 DB가 없어 해당 integration은 BLOCKED다.

## 최종 88질의 × 3 Detector × 3 Retriever 로컬 E2E

실제 네 모델의 같은 raw 후보를 재사용하고 모델 조합만 바꿨다. 질의는 evidence_v2로 처리하고 실제 KoE5/기존 DB(legacy_v6)를 검색했다. 동일한 실제 quality4 독립 재검사와 MockTransport를 모든 조합에 적용했다. E2E 비교는 기존 frozen query 비교와 별개의 **정책 혼합 평가**다. Retriever 3종의 결과는 각 모델 조합 안에서 모두 같았다.

| Detector (Retriever 3종 공통) | Query READY / 전체 | 검색 Hit@1 / Hit@5 (READY) | 검색 전체88 Hit@1 / Hit@5 | MRR (READY) | 최종 Mock E2E PASS / 88 | 보류·차단 |
|---|---:|---:|---:|---:|---:|---|
| own2 | 77/88 | 69/77 / 75/77 | 0.7841 / 0.8523 | 0.9286 | **12/88** | Gate REVIEW 7, Query guard 4, Context/output guard 65 |
| quality3 | 72/88 | 64/72 / 70/72 | 0.7273 / 0.7955 | 0.9236 | **11/88** | Gate REVIEW 13, Query guard 3, Context/output guard 61 |
| quality4 | 71/88 | 63/71 / 69/71 | 0.7159 / 0.7841 | 0.9225 | **10/88** | Gate REVIEW 14, Query guard 3, Context/output guard 61 |

- 최종 재검사에서 공개 V6 제어 문자열 `[ROLE_REFERENCE]`, `[REDACTED_UNCLASSIFIED]` 등만 원문 길이를 유지한 공백으로 처리했다. 실제 식별자·주변 텍스트는 그대로 검사한다. 합성 회귀로 제어 문자열 주변 이름/이메일이 계속 차단됨을 검증했다. 이 처리 전에는 14/13/12건, 후에는 위 12/11/10건 통과했다. 입력 문맥 변화로 오히려 추가 차단이 생겼으며 결과를 유리하게 선택하거나 기준을 완화하지 않았다.
- `RESIDUAL_PII_DETECTED`는 pattern/NER 보호 신호다. 실제 잔여 개인정보와 모델 오탐을 수동으로 구별한 결과는 아니다. 안전 신호가 남은 Context는 Mock 생성에도 보내지 않았다. **이 잔여 신호의 정밀도 검수/정책 보정은 BLOCKED(추가 로컬 검수 필요)**다.
- 최종 통과는 schema·인용 ID·실제 모델 출력 재검사 통과를 뜻한다. Mock의 정답 subject, 사실성, unsupported claim 비율, 실제 HCX 응답 실패율은 **NOT_MEASURED**. 답변 정확도 12/88 또는 100%라고 해석하지 않는다.
- 모든 조합에서 실제 API 호출/외부 개인정보 전송 0회, 운영 DB fingerprint 불변. 실제 사용자 Top-5와 QID별 상태는 요약 JSON에 원문 없이 기록했다. 기존 88질의 결과 파일은 덮어쓰지 않았다.

## 모델 비용 실측 비교

같은 고정 합성 문장, warm-up 후 30회씩, own2/quality3/quality4/team2를 round-robin으로 실행했다. 시간은 **raw 추론 + stdio + RSS sample 조회**이며 전체 RAG/HCX latency가 아니다. 모든 worker를 상주시킨 조건이므로 아래 비교가 모델 제거 시 RAM 절감 실측이라는 뜻은 아니다.

| Profile | warm p50 ms | warm p95 ms | Gate 사용 가능성 |
|---|---:|---:|---|
| own2 | 129.96 | 308.63 | 기존 V6 신호 확보 |
| quality3 | 196.89 | 401.02 | 기존 V6 신호 확보 |
| quality4 | 243.71 | 606.50 | 기존 V6 신호 확보 |
| team2 | 72.37 | 208.37 | raw 실추론 가능, V6 Gate 신호 부족으로 BLOCKED |

해당 실행 child sum-RSS 샘플 최대 2,099.67MiB. 187문서 corpus(문서 길이 다양, 별도 실행)의 worker 추론 p50/p95는 own2 420.83/744.15ms, KLUE 91.03/166.66ms, KoELECTRA 43.59/132.76ms였고, quality4 요청 전체는 585.46/1,147.08ms였다. corpus 실행 child high-water RSS는 own2 2,208.78MiB / KLUE 509.14MiB / KoELECTRA 371.91MiB. 같은 기기의 다른 조건/길이이므로 고정 문장 수치와 직접 비교하지 않는다.

## 발표용 추천과 허용되는 주장

**추천: own2 + own_v6를 로컬 비교 기준으로 유지. quality4는 보완 후보/추가 민감 유형 검수용 옵션. 운영 전환은 보류.**

근거: 이 seen_dev에서 own2의 Raw precision/recall/F1은 0.9151/0.9923/0.9521(FP84/FN7), quality3는 0.7890/0.9923/0.8791(FP242/FN7), quality4는 0.7586/0.9923/0.8599(FP288/FN7). pipeline strict 후보 P/R/F1은 세 방식 모두 0.9918/0.9320/0.9610(FP7/FN62)이었다. Gold 보호 이득은 관측되지 않았고 모델 추가 시 검수율·시간이 증가했다. 세 Retriever는 동일한 frozen-query 성능이며 own_v6만 원본 기준과 전수 parity를 확인한 경로다. 이는 최종 unseen 일반화/운영 모델 우열의 증거가 아니다.

가능한 주장: 4모델 로컬 실행/명시적 extra-sensitive 분기, 원본 187문서 동작 보존, 기존 187행을 쓰지 않고 실제 88질의 검색 평가, 동일 데이터/분모 비교, 재검사 fail-closed, 실제 KoE5/새 합성 인덱스/Mock 통합.

불가한 주장: 모든 데이터 PII 완전 보호, quality4 무조건 우수, 새로운 Blind 성능, 88질의 답변 정확성 검증 완료, 실제 HCX 성공, 전체 E2E 100% 통과, Entity 소유 관계 자동 복원, 운영 준비 완료.

## 재현 명령 (유료 호출 없음)

```bash
cd /Users/gojunseo/AegisRAG-unified-v1
AEGIS_FINAL_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag/bin/python
AEGIS_RETRIEVAL_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python
AEGIS_MODEL_PY=/tmp/aegis-unified-followup-model-env/bin/python
AEGIS_PRIVATE=/Users/gojunseo/AegisRAG/step2_privacy_rag/local_eval
AEGIS_FINAL_ENV=/Users/gojunseo/AegisRAG/.env

# 기존 환경을 바꾸지 않고 별도 모델 worker의 실제 4모델 검증/비용 계측
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FINAL_PY" -m unified_v1 --check-models --runtime-python "$AEGIS_MODEL_PY" --report-dir reports/unified_v1
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FINAL_PY" -m unified_v1 --benchmark-profiles --runtime-python "$AEGIS_MODEL_PY" --report-dir reports/unified_v1
# 실제 187문서·로컬 Gold, 원본 V6 parity 포함: 원문은 worker stdin에만 전달
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FINAL_PY" -m unified_v1 --evaluate-step1 --runtime-python "$AEGIS_MODEL_PY" --documents "$AEGIS_PRIVATE/holdout_labeled.jsonl" --report-dir reports/unified_v1
# 실제 KoE5/기존 187행, 같은 frozen queries와 Gold로 Retriever만 비교
PYTHONPATH=step2_privacy_rag/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "$AEGIS_RETRIEVAL_PY" -m unified_v1 --evaluate-retrieval --env-file "$AEGIS_FINAL_ENV" --artifact "$AEGIS_PRIVATE/koe5_v6_private/sanitized_eval.json" --report-dir reports/unified_v1
# 새 v2 질의 정책과 legacy DB의 전체 88질의 3×3 실제 로컬 비교 + Mock HCX
PYTHONPATH=step2_privacy_rag/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "$AEGIS_RETRIEVAL_PY" -m unified_v1 --e2e-local --compare-e2e --runtime-python "$AEGIS_MODEL_PY" --env-file "$AEGIS_FINAL_ENV" --queries "$AEGIS_PRIVATE/holdout_retrieval_queries.jsonl" --artifact "$AEGIS_PRIVATE/koe5_v6_private/sanitized_eval.json" --report-dir reports/unified_v1
# 새로운 합성 텍스트의 실제 4모델·KoE5·분리 인메모리 인덱스 + Mock HCX
PYTHONPATH=step2_privacy_rag/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false "$AEGIS_RETRIEVAL_PY" -m unified_v1 --e2e-isolated-synthetic --runtime-python "$AEGIS_MODEL_PY" --report-dir reports/unified_v1
# opt-in 없는 live 옵션은 NOT_RUN_BY_POLICY / exit 2 / 실제 호출 0회
PYTHONPATH=step2_privacy_rag/app "$AEGIS_FINAL_PY" -m unified_v1 --synthetic-hcx-live
```

--e2e-local만 쓰면 기본 고정 합성 질의 또는 --queries/--limit로 지정한 로컬 질의를 실행한다. --profile/--retriever로 조합을 선택할 수 있다. --compare-e2e에서는 동일 raw snapshot을 공유한 명시적 비교이므로 세 profile을 모두 평가한다. 임시 모델 venv는 `/tmp` 정리 후 재생성해야 할 수 있으며, 고정 버전 설치는 별도 venv에만 수행해야 한다. 실제 opt-in live 호출 명령은 실행하지 않았고 개인 입력 경로를 사용할 수도 없다.

## 실제 모델 아티팩트/환경

| Source | 고정 revision 또는 로컬 위치 | 관측 model.safetensors SHA-256 |
|---|---|---|
| A.X | c0f99b1dc113befefeff1a3091087d50bc3ea635 | b066442350d1436d413b5254b52dffea65f050132b35f8491542801ec1af79fc |
| Veil | e6e7d20fa9baa43a938f5f6cc780d3f2c9650fb9 | 2f355f9c05fd144a09364b2c1aa4f4729adac22aada576920d08ebc81773c31d |
| KLUE | private-models/aegisrag/roberta_small_v1 | 05dd28640e2c884007b7c7668ad1e2f770ac2cabe0d31cc9cfe6ac02d2b69b4f |
| KoELECTRA | private-models/aegisrag/koelectra_ft5 | fd2a89454ef29acac263ce3ea3629ee6b5eb513216d46f599462e5b179922abc |

config/tokenizer/Veil decoder 파일 SHA는 reports/unified_v1/final_summary.json에 포함한다. KoE5 pin 237b580aea358e3042fcf5baf78124c33c3afd16, dim1024, query:/passage: prefix/normalize/max512 유지. 모델 worker는 격리 torch2.2.2/transformers4.55.4, 실제 KoE5/DB parent는 기존 aegisrag-retrieval(동일 torch/transformers, sentence-transformers3.4.1)을 사용했다. 기존 aegisrag의 transformers5.22.2/tokenizers0.23.3/hub2.4.0은 변경하지 않았다. 플랫폼 Intel macOS x86_64, 측정은 이 로컬 기기/조건에 한정한다.

## 최종 테스트·안전 경계·남은 작업

| 검증 | 결과 |
|---|---|
| 기존 Step1 | 31개 실행, 30 PASS / 원본 release notebook 부재 1 SKIP |
| 기존 Step2 / 루트 | 183 / 29 PASS |
| Unified 전체 | **72 PASS** (기존 57 + Final 15) |
| 합계 | **315 실행, 314 PASS / 1 SKIP / 최종 FAIL 0** |
| 실제 4모델/KoELECTRA decoder/long offset | PASS |
| 과거 187문서 실제 V6 상태/HMAC/마스크 | PASS |
| frozen 88질의 실제 검색/85개 Top-5 parity | PASS |
| 88질의 3×3 실모델/DB/Mock | 실행 완료, 개별 PASS/REVIEW/BLOCKED는 위 표; 전체 통과 아님 |
| 새 합성 실제 KoE5 인메모리 index/Mock E2E | PASS |
| 신규 격리 PostgreSQL integration | BLOCKED — 확인된 폐기 가능 DB 없음 |
| 실제 HCX / 개인 문서 외부 전송 | NOT_RUN_BY_POLICY, 0회 |

안전 검사: 원본 V6/main/requirements.lock/HMAC/Region/운영 DB/타 worktree/팀원 참조를 변경하지 않았다. 실제 .env 값은 읽기 전용 자격 확인/로컬 키 연산/소스 유입 검사에만 사용하고 복사·로그·커밋하지 않았다. 원문·평가 Gold·원시 예측 span·가명 매핑·가중치는 Git에 추가하지 않았다. summary JSON은 집계·opaque QID/doc_id·실측/파일 전체 hash만 포함한다. GitHub push/PR/병합과 로컬 커밋은 실행하지 않았다.

수정/분석 이력: 기존 nullable tuple logits/라벨 29종 호환성은 어댑터에서 처리; REVIEW의 원본 mask 보존 보강; Region 다중 키 parity 수정; lookup 후보 rank 승격(동일인 exact 승격 아님); 제어 redaction literal의 재검사 입력 처리와 주변 PII 회귀; libproc RSS 측정으로 불필요한 tokenizer fork 경고 제거. 격리 DDL provenance comment의 SQL quoting도 Mock 성공 경로에서 검증·보완했다. 정상 worker 종료는 stdin EOF로 atexit 정리를 실행하고, timeout/fault만 강제 종료하도록 보완하여 cleanup 회귀를 통과했다. 요약 생성의 최초 기본 Python metadata 오류는 기존 명시적 interpreter로 바꿔 해결했다. 안전 조건이나 기존 테스트 기대값은 완화하지 않았다.

다음 작업은 재검사 차단 신호의 실제 잔여 PII/오탐 분리 검수, 폐기 가능한 별도 DB의 pgvector integration, 승인된 raw 소유 관계가 있는 Entity sidecar coverage 향상, 고정 프로파일 이후 새 Blind, 필요할 때만 명시적으로 승인한 합성 HCX 호출이다. 실제 답변 subject/claim/citation 사실성을 측정하기 전에는 운영 전환을 권하지 않는다.

**F5에서 종료.** 변경은 feature/unified-v1 worktree의 미커밋 상태이며 자동 push하지 않았다.

## 후속 GitHub 게시 요청

F5 종료 후 사용자가 feature/unified-v1의 커밋 및 GitHub push를 명시적으로 요청했다. 위 미커밋/미push 기록은 F5 종료 시점이며 이번 후속 게시 범위는 Final 구현·테스트·기준 명세·PII-free 실측 요약이다. main 병합/PR 생성/유료 호출/운영 DB 쓰기는 포함하지 않는다.
