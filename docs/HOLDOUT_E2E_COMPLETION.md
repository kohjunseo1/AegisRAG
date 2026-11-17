# Holdout 187 / 88 — E2E 완성 점검표 (2026-11-17)

> **우선 순위:** 다른 팀과의 기능 경쟁이나 신규 Blind 튜닝보다 기존 Holdout E2E를 먼저 동결한다.
> 문서 원문·Gold·API 키·HMAC 키는 GitHub에 올리지 않는다. 운영 5433 DB 및 기존 187행은 수정하지 않는다.
> 아래 '준비'·'검증'은 다르며, GitHub CI 통과를 실제 모델/DB/HCX 검증으로 해석하지 않는다.

## 1. 동결 기준

- 원본 Holdout 187문서, 질의 88, Gold 88.
- 기존 기록: 자동 문서 177 / 검토 10, 온라인 질의 READY 85 / REVIEW 2 / BLOCKED 1.
- 로컬 187문서 인덱스가 승인된 177+10을 포함하는지 **실제 확인이 필요**하다.
- 온라인 검색 기준: READY 85개 중 Hit@1 77/85, Hit@5 83/85; 전체 88개 중 77/88, 83/88.
- 검색 Hit@5와 LLM 답변 정확도는 별개의 지표다. 현재 전체 88질의의 HCX 사실성/인용 정확도는 검증 완료로 표시하지 않는다.

## 실측 업데이트 — 2026-11-17 사용자 로컬 Holdout 실행

> 증거: 사용자 터미널의 `prepare_holdout_e2e.py --check-only` 및 `verify_v6_e2e.py --eval-raw` 출력.
> 아래 결과는 해당 로컬 실행에서 보고된 값이며 GitHub CI 결과나 HCX 답변 평가로 대체하지 않는다.

| 단계 | 로컬 실측 결과 | 상태 |
|---|---|---|
| 187문서 / 88질의 / Gold 88 무결성 | 문서 187, 질의 88, Gold ID 전체 연결, 전 질의에 relevant_doc_ids 존재 | PASS |
| 운영 라이브 인덱스(읽기 전용) | `rag.aegis_live_docs` 187행, `key_matches=true`, READY | PASS |
| 실제 Step1/Gate 질문 88건 | READY 85 / REVIEW 2 / BLOCKED 1 | 재현 성공 |
| READY 검색 Hit@1 / Hit@5 | 77/85 = 0.9059 / 83/85 = 0.9765 | 기존 기준선 일치 |
| 전체 88질의 Hit@1 / Hit@5 | 77/88 = 0.8750 / 83/88 = 0.9432 | 기존 기준선 일치 |
| READY MRR / Recall@5 | 0.9371 / 0.8848 | 기존 기준선 일치 |
| 혼동 문서 Top-5 | 27/85 = 0.3176; 혼동 후보 존재 29건 중 27건 | 답변 위험 진단 필요 |
| 모델 Preload | 38,195.59ms | 실측 1회 |
| READY 85건 Warm Query Latency | p50 544.77ms / p95 675.48ms (HCX 제외) | 로컬 실측 |

- Gate 보류/차단: **Q001, Q015, Q033**. 실제 상태의 정확한 구분은 Gate 상세 코드로 진단한다.
- Top-5 검색 실패: **Q055** (첫 정답 41위), **Q065** (첫 정답 8위).
- Top-1 실패: Q004, Q053, Q067, Q069, Q078, Q082. 원인 확정 전에 QID별 boost/allowlist 튜닝 금지.
- 기존 187행 운영 인덱스는 **읽기 전용**으로 확인했고, `--eval-raw`에서는 수동 질의 승격을 적용하지 않았다.
- **아직 미검증:** 실제 문서 REVIEW 10건의 수동 교정/격리 DB 적재, 실시간 질문 CLARIFY 시나리오 E2E, HCX 88질의 사실성/인용/출력 안전성, 새 Blind 일반화.
- 다음 작업: 기존 로컬 `feat/review-diagnostics` 미커밋 변경을 보존하고 수동 REVIEW의 회귀 테스트 및 5544 격리 DB E2E를 우선한다.

## 임시 최종 실행 게이트 (2026-11-17)

로컬 미커밋 REVIEW 변경을 보존하고 **추가 브랜치 없이** `scripts/holdout_finalizer.py`로 다음 구간을 한번에 검사한다.

```bash
# 원래 ~/AegisRAG 폴더가 아니라 E2E Worktree에서 실행
cd ~/AegisRAG-holdout-e2e
git pull --ff-only

# 1. Veil-only PERSON 명사 수식형 인용 vs 서술형 인용 충돌 안전 교정,
#    모든 합성 단위 테스트 + Smoke. 실패하면 정책 파일 자동 복구
python scripts/holdout_finalizer.py policy

# 2. 실제 원본 187문서 → Step1/Gate → 서명된 로컬 검토대기열
#    기존 stage가 있으면 덮어쓰지 않고 상태만 확인
python scripts/holdout_finalizer.py stage

# 3. 기존 검증된 187행 라이브 DB를 읽기만 하여 88질의 검색 재현
#    HCX 호출 없음, Gold는 채점에만 사용
python scripts/holdout_finalizer.py query
```

세 단계를 순서대로 한 번에 실행하려면 `python scripts/holdout_finalizer.py all` 사용. 실행 전에 실수로 운영 DB를 쓰지 않는지 걱정하지 않아도 되도록 **이 스크립트에는 DB 쓰기 및 HCX 기능을 의도적으로 구현하지 않았다.**

**예상된 중단점:** 실제 문서 REVIEW/BLOCKED는 사람이 수정·재검증해야 하므로 스크립트가 임의 승인하지 않는다. 관리자 UI의 5544 격리 DB 커밋 및 HCX 전송은 별도의 명시적인 승인과 통과 기준이 필요하다. 따라서 `all` 성공은 **문서 staging과 기존 라이브 인덱스에 대한 로컬 질문 검색을 각각 검증한 것**이며, 새 staging 문서의 DB 적재·재검색·HCX 답변까지 이어진 단일 E2E 성공을 뜻하지 않는다.

**주의:** 신규 문서 staging은 `holdoute2e__...` 형태의 불투명 문서 ID를 사용한다. 이는 기존 Holdout DB의 `H0001...` ID와 동일하지 않으며, Gold 문서 ID를 이 신규 staging의 성능 채점에 그대로 쓰면 안 된다. 이 평가는 기존 `rag.aegis_live_docs`에 대해서만 수행된다.

## 2. 0단계 — 원본 입력·평가 Gold 검사 (외부 통신/DB/모델 없음)

```bash
python scripts/prepare_holdout_e2e.py --check-only

# 파일이 정확히 존재하고 무결성 검사를 통과한 경우에만:
python scripts/prepare_holdout_e2e.py \
  --out step2_privacy_rag/local_eval/holdout_e2e_cases.jsonl
```

- 검사 대상: `local_eval/holdout_labeled.jsonl`, `local_eval/holdout_retrieval_queries.jsonl`, `local_eval/holdout_retrieval_queries_gold.jsonl`.
- 파일이 다른 위치에 있으면 `--documents`, `--queries`, `--gold`로 안전한 로컬 경로를 지정한다.
- 문서/질의/Gold 중복·누락·ID 불일치와 Gold가 원본 코퍼스 밖을 참조하는 오류를 차단한다.
- 출력은 Git 무시 폴더 안에 **새 파일**로 생성하며 덮어쓰지 않는다. 원문 질문이 있으므로 외부 공유 금지.
- 준비 단계는 `expected_decision`을 임의 추정하지 않는다. Gold 문서가 있다고 HCX가 항상 ANSWER해야 한다고 가정하면 안 된다.

## 3. 1단계 — 실제 온라인 질문 Gate + 로컬 검색 재현

```bash
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live \
AEGISRAG_LIVE_MIN_ROWS=187 AEGISRAG_TOPIC_BOOST=0 \
python scripts/verify_v6_e2e.py --eval-raw

AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live \
AEGISRAG_LIVE_MIN_ROWS=187 AEGISRAG_TOPIC_BOOST=0 \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/holdout_e2e_cases.jsonl
```

- 위 두 도구는 문서 적재/DB 변경 없이 읽기 전용으로 질의·검색을 검증한다.
- `verify_v6_e2e.py --eval-raw`는 88개 질의의 실제 Step1/Gate 상태·검색을 재현한다.
- `eval_chat_mvp.py`는 준비한 88개 E2E 입력으로 검색 후보와 latency를 기록하지만, `--hcx-live` 없이는 LLM 답변 정확도를 측정하지 않는다.
- 실행 전에 `index_status`가 READY이고 행 수/key_tag가 일치하는지 반드시 확인한다.
- 운영 DB 접근을 확신할 수 없으면 실행하지 않고 `--help`, 합성 CI만 확인한다.

## 4. 2단계 — 문서 REVIEW 10건을 격리 DB에서 점검

- 테스트 대상 DB: `127.0.0.1:5544`, `aegis_blind_test` (운영 `5433`과 완전 분리).
- 원본 187문서를 신규 namespace로 stage하고 177 자동 승인/10 REVIEW가 재현되는지 확인한다. 환경·정책이 바뀌어 수치가 달라지면 사유를 조사한다.
- REVIEW/BLOCKED 문서는 관리자 UI에서 원문을 검토하고 **수정된 소스 → 동일 Step1/Gate 재검증 → 승인된 가명문서만 DB 반영**을 검증한다.
- 실제 REVIEW 10건에 대해 모두 수정이 가능한 것은 아니다. 안전하게 교정할 수 없는 문서는 적재하지 않는다.
- 최초 적재, 추가 승인/UPSERT, 반복 실행 멱등성, 승인 안 된 문서 거부, DB 대상 격리, API 권한을 검증한다.
- **주의:** Stage 재생성이나 승인 데이터 UPSERT가 기존 운영 187행에 영향을 주지 않도록 별도 DB의 행 수·접속 DSN부터 확인한다.
- 기존 `scripts/dataset_review_ui.py`, `scripts/dataset_intake.py`, `scripts/bootstrap_blind_db.py`, `test_review_ui.py`를 재사용한다.

## 5. 3단계 — 질문 REVIEW/BLOCKED와 근거 선별

- 질의 원문을 직접 `document_preparation.prepare()`로 보내는 현 구조의 REVIEW 이유를 안전한 상태 코드로 분해한다.
- 문서 REVIEW의 운영자 승인과 달리, 질문 REVIEW는 검증 가능한 로컬 복구/명확화/차단으로 처리한다.
- `query_recovery.py`의 특정 한글 이름·질의 표현 allowlist에만 의존하지 않도록 추가 PoC를 별도 실험으로 비교한다.
- 검증 불충분한 REVIEW→READY 승격 금지. 재검증에서도 탐지기/Gate 원인과 잔여 원문 개인정보 위험을 확인한다.
- 검색 결과 Top-5의 필수 HMAC·지역 조건과 질의에 실제 관련된 근거 구간을 점검한다. 근거가 없으면 HCX를 호출하지 않는다.

## 6. 4단계 — HCX 답변과 E2E 평가

- **실제 개인/업무 문서의 외부 HCX 전송 승인·잔여 개인정보 위험 평가가 끝나기 전에는 `--hcx-live`를 실행하지 않는다.**
- 먼저 공개 합성 E2E/HCX, 그다음 승인된 소수 실측 질의, 마지막으로 허용된 전체 88질의.
- 필수 지표: Gate coverage, Hit@1/5, Recall@5, MRR, Evidence Recall, ANSWER/CLARIFY/INSUFFICIENT 판단 적절성, 주장 단위 사실성, Citation Precision, 개인정보 누출, 잘못된 인물 근거 인용, E2E 지연 p50/p95.
- `eval_chat_mvp.py`가 만드는 개인 로컬 human-review 템플릿으로 실제 답변을 검수한다. 인용 ID 유효성은 주장 사실성의 충분조건이 아니다.
- 실패 원인을 문서 미적재 / Gate REVIEW / 검색 누락 / 근거 선별 실패 / HCX 답변 실패 / 출력 위반으로 분류해 수정한다.

## 7. 임시 최종 승인 조건

1. 187/88 입력 무결성 PASS, Stage/수동 검토/승인/재적재 경로를 **격리 DB에서 실제** 확인.
2. 88질의 실제 온라인 Gate 상태·로컬 검색 결과를 기록하고 기존 기준선과 차이가 나는 사례 분석.
3. 모든 질문에 ANSWER 또는 안전한 CLARIFY/INSUFFICIENT/BLOCKED 혹은 명시적인 오류 코드가 반환된다.
4. HCX 실행이 승인된 범위에서 사실성·인용·개인정보 검사 결과와 실패 목록을 저장.
5. 예외·무관 문서·다른 인물·모호한 질문·개인정보가 남은 질의에 대한 안전성 회귀 통과.
6. GitHub CI, 로컬 실모델, DB, API 테스트 결과를 별개로 기록하고 성능 수치를 과장하지 않는다.
7. 운영 DB/환경 파일/키/모델의 기존 상태를 보존하고 검증된 수정만 통합한다.

## 8. 이후 차별화 아이디어 백로그 — **지금 구현하지 않음**

동일 공개 코드 비교 대상:

- 4팀: https://github.com/BBuSang/likelion-AI-4team/tree/main — 동명이인 후보/역할 기반 검색, ask_back, 근거 선별, 출력 검사, E2E 평가, 에이전트 계획.
- 3팀: https://github.com/taewoo26/nlp_like_lion_chatbot_03/tree/develop — 역할·지역 기반 검색, 문서 접근 정책, 인물 후보 분석, 토큰 일관성/검색 평가.

따라서 역할 필터, 동명이인, 단순 ASK_BACK, 단순 Agent만을 독자적 성과로 주장하지 않는다.

후보 (E2E 동결 이후에만 실험):
1. **Privacy–Utility Pareto:** 마스킹 커버리지·오탐·REVIEW율·검색/답변 가능률을 같은 조건에서 비교해 정책별 효과를 분석.
2. **Risk-Adaptive Verification:** Step1/Gate 불확실성에 따른 선택적 독립 검증; 실패 시 보류, 승인 우회 불가.
3. **Claim-Level Privacy-Grounded Verification:** 근거 ID 검증을 넘어 답변 주장-근거 구간 단위의 지지 여부/타인 근거 인용 측정.
4. **Cost-Aware Dual-Detector Serving:** 캐시·모델 워밍업·선택적 추가 검증에 의한 p50/p95 개선 및 보호 동등성 검증.

차별점은 새로운 명칭이 아니라 **동일 기준선의 before/after, ablation, 안전성·지연시간·답변 정확도 수치**로 입증한다.
