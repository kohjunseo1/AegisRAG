# AegisRAG Unified V1 — 최종 베이스라인 탐색·선정 실험 명세

> **목표:** 두 팀의 모든 로직을 유지하는 것이 아니라, **안전성·성능·처리율·속도의 실측 근거로 필요한 것만 채택**하여 발표 가능한 최종 베이스라인을 결정한다. 순서: **실전1 탐지·가명처리 확정 → 실전2 검색·HCX/로컬 E2E 확정**. 중간 결과가 나쁘면 기존 V6로 되돌릴 수 있게 한다.
>
> **코드 기준:** `kohjunseo1/AegisRAG` `feature/unified-v1` (`01313ab6c68874fee1fd7e4d689a6399ae993e66`), 팀원 `8bitHermitcrab/privacy-rag-chatbot` `feature/detect` (`242543bf0eae91aa3d655227a747aa128707368e`). 실행 시 SHA 다시 기록. 원본 V6/main 및 팀원 reference는 수정하지 않는다.

## 0. 실험 규율 및 데이터 고정

| 세트 | 크기/정답 | 용도 | 주의 |
|---|---|---|---|
| A. 기존 Step1 평가 | 187문서 / PII Gold 912 | 회귀·모델 선택·오류 분석 | **seen_dev**, 이미 반복 분석됨 |
| B. 사용자 제공 `blind_eval.jsonl` | 171문서 / PII Gold 1,439, 7종 | **데이터 분포 이동 확인** 및 잠금 검증 | SHA-256 `52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f`. 기존 탐색에 노출됐거나 생성 출처가 있으므로 무조건 *신규 Blind*로 부르지 말 것. 로컬 경로와 중복 지문 검사 |
| C. Step2 검색 | 기존 187문서 인덱스 / 질의 88개 + Gold | Retriever 및 E2E | 기존 READY 85·DEFERRED 3. 모든 방식의 **전체 88 분모**와 **READY/실제 검색 분모** 둘 다 표시 |
| D. 합성 반례 | 동명이인, 복성·띄어쓰기·조사, 부분 주소·번호, 오탐 업무용어, 추가 민감유형 | 안전성 회귀 / 설명 가능한 실패 분석 | 규칙 개발용. 기존 QID·고유 이름에 맞춘 하드코딩 금지 |

- 입력 정답 `entities`, `relevant_doc_ids`, `neutral_doc_ids`, `confusable_doc_ids`는 **평가에만** 사용; 탐지·정규화·검색·임베딩·후처리에 절대 입력하지 않는다. 먼저 ID/Span 유효성, 데이터 간 텍스트 중복, 모델 revision, tokenizer/worker, HMAC key tag, embedding pin, 인덱스 fingerprint, 평가 코드 버전을 고정·기록.
- **A로 탐색 → 정책 및 임계값 고정 → B로 검증**. B 결과를 보고 수정하면 B도 개발 데이터가 되며 별도 미공개 세트가 필요하다. 이미 확인한 표/실험 결과는 숨기지 않는다.
- 지표는 문서별 paired 비교, 동일 입력·Gold·모델 원시 출력 사용. 임의 seed/score 선택 금지. 가능하면 문서 단위 bootstrap CI 또는 최소 QID별 gain/loss를 함께 보고.
- 사용자의 기존 V6(`own2` + 원본 V6 Gate/가명처리 + `own_v6` + HCX-007)를 **Frozen Control**로 유지. 신규 방식이 더 나쁘면 유지하지 않아도 된다. 외부 LLM 유료 실호출/개인 원문 외부 전송은 별도 명시적 승인 전 금지.

## 1. 실전1: 4모델 탐지·Fusion·Gate 실험 (먼저 완료)

### S1-0. 정확히 무엇을 합치는가 — 팀원 코드에서 후보 분리

| 기능 | 소스 | 실험에서의 판단 |
|---|---|---|
| Frozen PII 보호·HMAC 96-bit, V6 Gate, 정규화/주소 Region | 우리 `pii_detector`, `privacy_gate`, `local_pseudonymizer`, `name_*` | **기준선/보호 하한**. 변경하려면 별도 parity·안전성 증명 |
| KLUE-RoBERTa 기본 BIO + 토큰 경계 | 팀원 `starter/pii_pipeline.py` | 모델 단독 성능/경계 검증, 유용할 때만 보조 |
| 이름 문자 확률·약한 이름 후보 검증 (`TAU_SPAN`, `TAU_CAND`) | 팀원 `starter/model_ensemble.py` | 기존 argmax 대비 추가 TP·FP·레이턴시 Ablation. 확률은 기본적으로 미보정 |
| KoELECTRA 59라벨 decoder, 7종 매핑·extra-sensitive | 팀원 `starter/hf_detector.py`, 우리 `unified_v1/adapters.py` | 독립 모델·민감유형 검증. 법적 유형 다른 `ALIEN_ID`를 RRN으로 강제 HMAC 변환 금지 |
| 이름 문맥·Kiwi NNP·후처리, KoELECTRA 합의/검수 분기 | 팀원 `starter/hybrid_detector.py` | **원본 로직 그대로** 먼저 재현; `TAU_ACCEPT=0.9`, `TAU_REVIEW=0.5`, `DROP_UNAGREED`, `NUM_UNION`, `NAME_FILTER` 옵션별 Ablation. 임계값·필터는 블라인드에 맞춰 튜닝하지 않음 |
| 이름/주소 구조 보완 | 팀원 `starter/detect_boost.py` vs 우리 `StructuralSentinel`, `ContextualBoost` | 한쪽을 무조건 합치지 말고 **개별 추가·제거** 테스트. 드문 성·복성·조사/번호 경계/상세주소의 실패 유형을 나눠 판정 |
| 사람 연결 | 팀원 `starter/person_resolver.py` vs 우리 `identity.py`/`sidecar.py` | 동명이인은 Step1에서 원시 역할·소유 근거의 **오병합 방지**만 검증. 이름만 같다는 이유로 연결 금지; 실제 검색 연결은 실전2에서 실험 |
| 팀원 제안 `V6 경계 + 하이브리드 라벨` | 별도 업로드된 `integrated_pipeline_v1_v2(1).md`의 설계안 | **기존 span-cache에서 보고한 가설/후보**. 실제 변환 코드·옵션·Gold·키·정규화 경로를 확인하고 재현하기 전까지 성능을 인정하지 말 것 |

**유지하지 않을 수도 있는 항목:** 불필요한 3·4번째 모델 상시 실행, 성능 저해 규칙, 미검증 동명이인 자동 결합, 유형을 잃는 숫자 공통 토큰, 보호 미확정 REVIEW의 자동 DB 적재, 실제 범위 밖의 ACL·Vault·UI 재구축. 각각 없애도 성능/안전성이 유지되는지 Ablation으로 결정.

### S1-1. 모델 자체의 기여도 — 공통 Raw Snapshot 1회

1. A.X, Veil, KLUE, KoELECTRA를 각 문서에 **한 번만 실추론**하고, 원문/Gold가 포함되지 않는 로컬 보호 캐시(또는 RAM)로 동일 후보를 재생. 실제 추론 시간과 재생 시간을 분리. 모델 별 원본 디코딩/오프셋/parity 확인.
2. **단일 4개 + 2모델 6개 + 3모델 4개 + 전체 1개 = 총 15개 비공집합**을 Raw 후보 **Union**으로 평가. 이때 V6 Gate가 A.X/Veil을 요구한다고 다른 조합을 BLOCKED하여 Raw 성능이 낮다고 기록하지 말 것.
3. 별도 **Agreement(모델 간 동일 라벨·경계/중첩)**를 분석하되 AND만 남기는 탐지를 안전한 기본값으로 채택하지 않는다. 모델별 추가 Gold, 공통 누락 FN, 순수 FP, 경계 차이, 타입 충돌, 불확실 후보의 분포를 구분.
4. 모든 모델/조합: **Strict micro TP/FP/FN/P/R/F1, 라벨별 F1, 최소 하나 맞춤/경계만 틀림, 완전 미탐, 새로 보호 가능한 Gold, 과잉 마스킹 후보, 모델 소요시간/RSS**. Raw 값과 Gate 결과는 같은 표에 섞지 않는다.
5. 15조합은 후보 기여 분석용일 뿐, 전부 운영 Gate/E2E로 올리지 않는다. **추가 모델의 한계 기여가 없거나 FP·시간만 늘면 탈락 가능**.

### S1-2. Fusion **알고리즘** 비교 (핵심 실험)

동일 Raw Snapshot에서 다음 정책을 재생한다. 순서대로 작게 구현하고, 각 정책 변경 근거를 로그에 남긴다.

| ID | 정책 | 핵심 질문 |
|---|---|---|
| F0 | **Frozen V6** (A.X+Veil + 기존 Gate) | 기준점: 정확성, 보호, READY, HMAC, 속도 |
| F1 | **Raw Union + 현재 evidence_v2** | 기존 quality3/4에서 오탐·REVIEW가 왜 늘었나? 보호 기여는 있나? |
| F2 | **팀원 원본 RoBERTa(+ensemble/boost) + KoELECTRA Hybrid** | 원본 코드·실제 옵션 그대로 단독 재현했을 때 어느 유형에서 이기나? |
| F3 | **V6 Boundary + Hybrid Label/후보 제안** | V6로 검증된 보호 경계는 유지하면서 추가 후보·라벨을 안전하게 채택할 수 있나? 충돌 시 검수로 분리 |
| F4 | **Type-aware Evidence Fusion** | 전화·이메일·번호는 canonical/형식·문맥 검증, 이름·주소는 경계·역할·복성·문맥 근거로 분기할 때 TP/FP/READY가 개선되나? |
| F5 | **Conditional Cascade** | 기본 V6 후, 미탐 위험/경계 충돌/REVIEW 등 **안전하게 정의된 조건**에서만 KLUE/KoELECTRA 실행하여 보호·정확도는 유지하고 P95를 낮출 수 있나? |

- **중요:** 팀원의 `TAU_ACCEPT`는 모델별 calibration을 보장하지 않는다. Agreement/Confidence만으로 HMAC 확정 금지. `V6 Boundary + Hybrid Label`에서 라벨을 바꾸면 HMAC Label Scope와 검색 토큰 동등성이 무너질 수 있으므로 **Label-changing 후보는 명시적 typed validation 및 문서·질의 토큰 parity**가 확인될 때만 확정.
- 어떤 정책이든 결과를 `CONFIRMED_HMAC / MASK_ONLY_PENDING_APPROVAL / REVIEW_REQUIRED / BLOCKED`로 분리. 문서 보호 Span이 Gold 전체를 덮는지와 **각 Gold 개체를 100% 가렸는지** 별도 측정. 미검증 후보를 감추고 READY로 승격하지 않는다.
- 팀원 설계안의 **'REVIEW도 넓게 가리고 자동 적재'**는 기본 채택 금지. 보호 경계의 완전성, 독립 재검사, 동명이인·키·버전 정합성과 명시적 적재 승인 없이는 격리 시험용으로만 다룬다.
- **단계별 진단**: 추가 후보 수 → 신규 TP/FP → 경계/라벨 충돌 → HMAC 확정 수 → Mask-Only → READY/REVIEW/BLOCKED → 실제 indexable 문서 수. 보호 문자율만 100%여도 READY 0이면 승격 불가.

### S1-3. 데이터별 검증과 실전1 선택 게이트

- A(187)에서 F0~F5 중 Pareto 후보 최대 **2개** 선별: ① 기존 V6 대비 완전 미탐/완전 가림/오승격·오병합 안전성 악화 없음 ② 실사용 가능한 READY/Indexable coverage 악화 최소 ③ Strict F1·FP/FN 유의미한 이득 ④ P50/P95/RSS 비용 수용 가능. **성능 향상은 가설이지 보장치가 아님.** 이득 없으면 F0 유지.
- B(171, 1439 Gold)는 **A에서 고정한 후보 및 Frozen Control**만 비교. 재사용/관찰 이력 확인. 유형별 표·길이 분포·실패 유형·문서 paired win/loss 작성. 결과를 보고 재튜닝하면 '선정 이후 검증' 자격 상실을 표시.
- 결과에 **Raw NER F1 / Pipeline strict F1 / Gold 완전 개체 보호율 / FP·FN / HMAC 정확도 및 일관성 / READY·REVIEW·BLOCKED / 자동 적재 가능 수 / cold/warm P95**를 분리. 아직 안전하지 않은 자동 승인으로 READY를 인위적으로 늘리는 방식은 탈락.
- **중단/진행:** 합리적인 S1 후보가 정해지기 전 실전2 신규 인덱스 생성·HCX 변경 금지. 기존 V6 회귀가 실패하면 실패를 해결하고 S1을 다시 측정. 코어 V6 변경 대신 별도 실험 정책으로 구현.

## 2. 실전2: Retriever·동명이인·HCX/E2E (S1 후보 최대 2개만)

### S2-0. 기존 결과와 팀원 점수의 공정성부터 확보

- **팀원 표**는 동일 88질의에서 `V6 경계 + Hybrid 라벨`의 Hit@5=1.000 등을 보고했지만, `V6` 대상 SHA/키/문서 구성/청크/가명 질의·Gold/Top-10 원본이 확인되지 않았다. 수치는 **재현 전 참고**만 한다. 팀원 `deliverables/retrieval_eval_v3.md`에 공개된 과거 v3 실측(0.773/0.920)과도 실행 조건이 다르다.
- 팀원 `starter/search.py`는 **`rag.chunks` 청크별 Dense 검색 → Token ALL+Region → ALL → ANY → Region → Dense 그룹 병합 → 문서 중복 제거**. 우리 `unified_v1/retrieval.py`는 **`rag.aegis_live_docs` 문서별 후보 통합 및 정렬**. 같은 `team_v3`라는 라벨을 써도 **원본 v3 재현이라고 부르지 말 것**.
- 평가 스크립트는 양쪽 모두 Neutral 문서를 **채점에서만 제외**하는 방식이므로 이것만으로 차이를 설명하지 않는다. **Top-10, neutral 반영 전 실제 랭킹/반영 후 점수**를 각각 저장. Hit/Recall/MRR 정의, N=88 vs READY=85 분모 및 누락 질의 처리 통일. **모든 비교에서 QID별 Top-10과 개선·악화 QID를 출력**.

### S2-1. 실험 변수는 한 번에 하나만 바꾼다

| 비교 | 고정 | 바꾸는 것 | 확인할 지표 |
|---|---|---|---|
| R0 Frozen 재현 | 기존 187행, KoE5, 동일 V6 가명 질의·Gold | 없음 | 원본 Top-5 85/85 parity, 기존 DB fingerprint 불변 |
| R1 Retriever-only | 문서 텍스트·HMAC·Embedding·Gold·QID 고정 | `own_v6`, 팀원 **원본 그룹 우선순위의 문서용 재현**, Unified Hybrid | Hit@1/5/10, Recall@5/10, MRR, Confusable@5/10, QID delta |
| R2 문서 vs 청크 | 보호 텍스트/모델/질의·Gold 고정 | **동일 문서를 격리된 청크 인덱스로 재색인**, 같은 그룹 검색 비교 | 청크로 얻고 잃는 QID; 문서 중복 제거 및 Context 품질. **원본 rag.chunks를 우리 운영 인덱스와 동일하다고 가정 금지** |
| R3 가명화 영향 | Retriever/인덱스 형태·골드 고정 | 문서·질의 모두 Frozen V6 또는 S1 최종 후보로 **일관되게 재처리** | 토큰 충돌·불일치, indexable coverage, Hit/MRR; 변경된 텍스트는 별도 폐기 가능 인덱스 |
| R4 Embedding 영향 (선택) | 같은 가명 데이터·청크·ranking | KoE5 vs 팀원 원래 embedding/API | 비용·지연·품질; 승인 없는 외부 전송 금지, 동일 모델 아닐 때 결과 분리 |
| R5 Identity 후보 | 같은 검색 결과 | `legacy_lookup_key`, 검증된 `entity_id`, CLARIFY 정책 | 올바른 대상 선택/오병합/불필요한 CLARIFY. Source ownership 없는 Entity를 확정하지 않음 |

- **현재 코드 버그 수정이 선행:** `identity.py`의 원문 이름 기반 Lookup Key와 `sidecar.py`의 `<PERSON_...>` 토큰 기반 Lookup Key가 달라 실제 질의-문서 Lookup이 불일치한다. **동일 의미의 HMAC 도메인·입력 정규화를 하나로 통일**하고 문서·질의 재현 테스트. 기존 토큰을 깰 수 있으면 별도 버전 Sidecar로 처리. `e2e_compare()`에도 실제 `name_lookup_keys`, `entity_ids`, 모호성 신호를 전달.
- `ExistingIndexReader`의 실DB `team_v3` 결과가 `own_v6`와 같더라도 팀원 v3의 청크 기반 결과와 동등하지 않다. Top-10을 위한 limit(현 코드 최대 5) 확대는 read-only·bounds 검증을 유지하며 추가.
- 지표는 **Eligible 85/Full 88/실제 송신된 Top-k**를 구분. 검색하지 못한 Q001 등은 분리 표기. 'Hit@5 1.0'이 개인정보/사람 정답·답변 사실성 1.0을 뜻하는 것은 아님.

### S2-2. 로컬 E2E 12/88 차단 원인 해결 (검사 무조건 완화 금지)

- 현재 88질의 own2→Mock E2E 최종 12 PASS, 7 Gate REVIEW, 4 Query Guard BLOCK, 65 Context/Output Guard BLOCK. 이는 **실제 HCX 실패가 아니라 보안 재검사 차단**. 동작 전 단계까지의 실모델·KoE5·DB 실행은 있었으며 실제 API 전송은 0회.
- 각 QID마다 원문 저장 없이 `gate → query prep → local query recheck → retrieval → context recheck → outbound → Mock generation → output recheck`의 **처음 실패 단계**, 모델/규칙/라벨·Span 위치/차단 사유(민감 원문 제외) 집계. 사람 검토용 상세 진단은 **Git 외부 로컬 보안 경로**에만.
- 실패를 `REAL_RESIDUAL_PII / FALSE_POSITIVE / POLICY_MISMATCH / TOKEN_OR_VERSION_MISMATCH / UNKNOWN`으로 **증거 기반** 구분. 실제 잔여 정보가 아닌 V6 제어문자·토큰만 별도 deterministic 처리; NER가 개인정보라고 판단한 일반어를 무근거로 화이트리스트 금지.
- 보호 경계가 완전하지 않은 문서·질의 또는 Identity 불확실성은 계속 fail-closed. 감소한 차단율과 **신규 누출·거짓 승인 여부를 동시에** 확인. `Mock E2E PASS`는 답변 정답률이 아님.

### S2-3. HCX 생성과 최종 선정

- S1/S2 로컬 가명처리·독립 송신검사·SQL 검색을 통과한 **승인된 합성 데이터**만 HCX-007 실제 1~2회 선택적 실호출(명시적 사용자의 opt-in·예산 상한). 기존 `.env`/`hcx.call_hcx()` 재사용, 무승인 실호출 0회. 별도 사용자 승인 전 **Mock + 로컬 평가까지만** 완료.
- 답변 평가: 주체 정합성, 인용 문서의 관련성, 주장의 근거 충실도, 근거 없는 내용, 개인정보 재노출, CLARIFY 정당성, schema 실패/중단율. JSON Schema 통과 = 사실성 통과가 아님. 실제 88질의 전체 HCX 응답 품질은 **NOT_MEASURED**로 유지하다 평가 자료가 있을 때만 주장.
- 최종 후보는 **탐지 1~2 × Retriever 1~2**까지만 전체 E2E. 원본 V6보다 안전성·검색·답변/레이턴시 개선이 실증되지 않으면 기존 `own2 + own_v6`를 베이스로 확정해도 됨. '4모델을 전부 쓰는 것'을 목표로 두지 말 것.

## 3. 구현 순서 · 완료 게이트 · 결과물

| 순서 | Codex 작업 | 반드시 남길 근거 | 다음 단계 조건 |
|---|---|---|---|
| **P0** | SHA/데이터 provenance·중복/Gold 확인, 실제 모델·팀원 코드 옵션 고정, 평가기 정의 일치 확인 | `EXPERIMENT_MANIFEST.json`(비밀/원문 없음) | 4모델·환경/데이터 조건 확정 |
| **P1** | S1-1 4모델 단독+15개 union Raw 분석 | `step1_raw.csv`, 모델별/라벨별/데이터별 FP/FN·신규 TP 요약 | Raw 분석으로 핵심 결함과 기여 확인 |
| **P2** | S1-2 F0~F5 Fusion 정책 독립 구현/Ablation 및 A→B 검증 | `step1_fusion.csv`, 보호/Gate/latency 회귀, 결정 근거 | 안전성 통과 후보 최대 2개 또는 V6 유지 |
| **P3** | S2 R0~R2 Retriever 공정 비교·Top-10/청크·분모 통일 | `retrieval_same_input.csv`, QID별 Top-10/증감, 원본 재현성 | 동등 비교가 성립해야 R3 진행 |
| **P4** | R3/R5, lookup·동명이인, E2E 재검사 88개 원인 분석 | `e2e_gate_diagnostics.csv`, 정책별 처리율/false positive 수동검수 상태 | 신규 누출·오병합 없이 로컬 Mock E2E 개선 확인 |
| **P5** | 통과 후보만 Mock HCX·응답 평가 준비/발표 요약 | `FINAL_BASELINE_DECISION.md` 1개, 설정·성능·실패·선택/폐기 이유, 복붙 CLI | 실제 HCX는 사용자 별도 허가 후 |

- **최종 결과 파일 위치**: `docs/unified_v1/FINAL_BASELINE_DECISION.md` + `reports/unified_v1/selection/`(PII 없는 집계). 문서 수를 불필요하게 늘리지 말 것. QID/doc_id의 불투명 ID와 메타데이터는 가능하지만 원문·예측 원시 Span·Gold·HMAC 키·가중치·`.env`는 Git에 포함하지 않는다. 로컬 민감 상세 파일은 `.cache/aegisrag/unified_v1/private/` 등에 0600 보관.
- 실측 구분: `REAL_MODEL_RAW`, `REAL_MODEL_FUSION`, `REAL_DB`, `ISOLATED_MEMORY`, `MOCK_HCX`, `LIVE_SYNTHETIC_APPROVED`. 미실행·미재현은 `BLOCKED/NOT_RUN`, 모델 선택을 유리하게 만들기 위한 데이터/분모 변경 금지.
- 운영 `rag.aegis_live_docs`는 **읽기 전용**. 새 보호 텍스트의 SQL 색인은 확인된 별도 폐기 가능 DB에서만 만들 것; 없다면 in-memory로 평가하고 SQL 통합은 BLOCKED. 기존 V6/`main`/다른 worktree/팀원 reference, HMAC 96-bit·KoE5·HCX-007 pin 변경 금지. 승인 없는 유료 호출, PR, push, merge 금지.
- 통계적으로나 논리적으로 개선이 입증되지 않은 복잡한 조합은 **폐기 가능**. 발표에서는 채택된 로직의 근거, 비용, 실패 사례, 안전성 제한을 투명하게 설명.

## 4. Codex 실행용 단일 지시 (본문 참조)

> 현재 `feature/unified-v1`에서 이 MD를 전체 읽고 **P0→P5** 순서로 수행해. 기존 V6를 기준으로 **모델 4개 단독/15개 조합 → 실제 팀원 Hybrid/새 Fusion 정책의 paired 평가 → 안전성·자동 처리율·속도 기준으로 후보 축소 → 동일 88질의 Retriever 공정 재현(청크/문서/Top-10/분모 분리) → 동명이인 및 12/88 재검사 병목 분석 → 최종 로컬 Mock E2E**까지 진행해. 독립 재현할 수 없는 팀원 점수는 추정·위장하지 말고, 유의미한 성능 향상이 없으면 원본 V6를 유지하도록 판단해. 코드를 필요 이상 병합하지 말고 유효한 로직만 채택해. 단계별 테스트/실측과 실행 CLI를 남기고 `FINAL_BASELINE_DECISION.md` 한 파일로 정리해. 실데이터·키·운영 DB·V6/main은 보존, 비공개 문서 외부 전송·실제 HCX·push·merge는 금지. 조건이 충족되지 않으면 그 실험만 BLOCKED로 기록하고 안전한 나머지는 계속해.
