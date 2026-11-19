# AegisRAG Unified V1 — 최종 E2E 구현·평가 명세

**기준:** `kohjunseo1/AegisRAG`, `feature/unified-v1`, 검토 HEAD `2d0bb5d` (2026-11-19). 현재 코드 보존·필요한 부분만 수정. 기존 `main`, V6, HMAC(96-bit/24 hex), 운영 187행, HF 모델 revision, KoE5, HCX-007 유지. 범위: 실전1 탐지/보호 + 실전2 검색/생성. ACL/복원·새 서비스 구축 제외.

**최종 목표:** 네 모델(A.X/Veil/KLUE/KoELECTRA) 실추론 → Evidence Fusion/Gate → HMAC/동명이인 → 기존 DB 또는 격리 신규 인덱스 검색 → HCX-007(합성 데이터만 명시적 허가 후 실호출) → 출력 재검사 → 재현 가능한 로컬 E2E/평가 CLI. 결과로 `own2` 대 `quality3` 대 `quality4` 및 검색 프로파일 중 발표용 기본값을 결정. **기준값은 실험 전에 고정하지 말 것.**

## 0. 새 KoELECTRA 가중치 — 실물 확인 사항 및 중요 호환성

사용자 제공 `koelectra_ft5.zip` 정적 검사: ZIP 무결성 PASS, `koelectra_ft5/`에 `model.safetensors`(56,309,164B), `config.json`, `tokenizer.json`, `tokenizer_config.json`, `train_info.json` 있음. `__MACOSX`는 무시. 모델은 `ElectraForTokenClassification`, 12 layers, 59 outputs = O + 29개 유형의 BIO. 체크포인트 `transformers_version=5.20.2`, tokenizer `BertTokenizer`, max length 메타데이터 256/512. **파일 무결성 PASS ≠ 실행/원본 예측 parity PASS.**

| 아티팩트 | 업로드 ZIP에서 확인한 SHA-256 |
|---|---|
| `config.json` | `6eb2b024f6144b5df04be1304997765aa8518420998ab1f3151a50f570ba0f91` |
| `model.safetensors` | `fd2a89454ef29acac263ce3ea3629ee6b5eb513216d46f599462e5b179922abc` |
| `tokenizer.json` | `6234fb96e9031ec5e6a83b27ba1ade973e823d5e34a62a49c8d00a20f49e09eb` |
| `tokenizer_config.json` | `8a4a9ae2d88ba2638f862c7df7564b38ac4b2849f24c850752a72b9a1e3908b8` |

- 팀원 원본 `../privacy-rag-chatbot-reference/starter/hf_detector.py`의 `KOELECTRA_MAP`, `decode`, `merge_adjacent`, `to_ours`와 비교. 현재 `unified_v1/adapters.py`의 `map_label()`은 29개 유형을 기본 지원하지 않으므로 **그대로 연결하면 UNKNOWN_MODEL_LABEL 위험**. 7개 핵심 라벨은 명시 매핑: `NAME→person`, `PHONE→phone_number`, `EMAIL→email`, `ADDRESS/ZIPCODE→address`, `RRN→resident_registration_number`, `CARD_NUMBER→card_number`, `ACCOUNT_NUMBER→account_number`.
- **`ALIEN_ID`는 팀원 원본에서 `resident_registration_number`로 매핑**하지만 법적·의미적 유형이 다른 외국인등록번호를 주민등록번호로 합치는 것은 재검토 필요. 규약 확인 전에는 별도 민감 후보+Mask/REVIEW로 처리하고 무단 HMAC 타입 승격 금지. `PASSPORT`, `CVC`, `IP_ADDRESS`, `USER_ID` 등 7종 외에도 민감할 수 있는 유형을 단순 삭제하지 말고 명시적 `EXTRA_SENSITIVE_CANDIDATE` 정책으로 분기. 비민감 `DATE`, `QUANTITY`, `POSITION` 등은 근거 있는 allowlist 처리. 동작 유형/라벨별 계수만 진단 로그에 기록.
- KoELECTRA 원본 라벨 `label2id/id2label` 완전성, tokenizer↔embedding vocab, tuple/ModelOutput logits, BIO offset(Unicode), 모델 재사용, 256/512/truncation/overflow, 합성 예제의 raw span 원본 decoder 비교 테스트. 원본과 다르면 정책 차이와 로더 차이를 분리해 보고. `train_info.json`의 최고 실험 수치는 현재 통합 성능으로 인용 금지.
- 모델 파일은 **Git 밖** `~/private-models/aegisrag/koelectra_ft5`에 설치. Codex는 사용자 Mac 다운로드 경로를 확인하고 SHA 검증. 없으면 체크포인트 자체를 Git/원격에서 찾거나 다른 모델로 대체하지 말 것. 기존 `aegisrag` Conda 환경 업그레이드 금지; 이미 확보한 격리 모델 환경을 점검해 재사용하거나 별도 생성.

## 1. 구현 수정 — 우선순위/완료 조건

| 순서 | 대상/파일 | 반드시 완료할 일 | 완료 판정 |
|---|---|---|---|
| **F0** | `runtime.py`, `adapters.py`, `worker.py`, `local_validation.py` | KoELECTRA manifest/hash 로딩 + 명시적 라벨 매핑; `team2`, `quality4`, `drop_*` 등 실제 가능 프로파일 실행. 상주 worker·에러 fail-closed·RAM/latency 계측 유지. 동시 작업/프로세스 종료 테스트. | **4개 실모델 합성 추론 PASS** + 라벨/오프셋/원본 decoder parity 보고. 불일치 미해결 시 `PARITY_BLOCKED` |
| **F1** | `replay.py`, `protection.py`, `pipeline.py` | 기존 V6 Gate의 동일 원시 후보 재사용, 중복추론 방지. `evidence_v2`의 부분 이름·주소/번호·경계 확장·서로 다른 타입 충돌 안전 처리. HMAC 확정과 Mask-Only 분리; 불확실 범위는 REVIEW/BLOCKED, 자동 적재 금지. | 기존 own2 Gate/Mask/HMAC 회귀, 4모델 Mask 보호 구간 단조성, 불확실 경계 테스트 PASS |
| **F2** | `identity.py`, `retrieval.py`, `postgres.py` | `name_lookup_key` 후보 탐색과 검증된 `entity_id` 분리. 원본 187행은 Entity/Lookup 정보가 없을 수 있으므로 **원본 변경 없이** 승인된 로컬 Sidecar를 생성/연결. 동명이인·동명 직원/고객·연락처 변경/공유·원문 부재 시 확정 금지, 필요할 때만 CLARIFY. | 잘못된 동일인 병합 0인 합성 반례, 실제 metadata coverage/UNRESOLVED 비율 보고 |
| **F3** | `postgres.py`, `embeddings.py`, `database.py`, `retrieval.py` | 기존 `rag.aegis_live_docs`를 **읽기 전용으로 재사용**, KoE5 revision/dim/key tag 검증. `own_v6/team_v3/hybrid_v1` 비교. 기존 DB 검색 parity 불일치면 **테스트 실패**. `legacy_v6` 적재 정책과 `evidence_v2` 질의 정책을 별도 provenance로 기록, 동등하다고 위장 금지. 새 보호 텍스트·Entity/청크를 인덱싱할 때만 폐기 가능한 별도 실험 DB/테이블. | 187행 fingerprint 보존, Top-5 parity assertion PASS, 88질의 검색 재평가 가능, 격리 인덱스 생성·삭제 안전 검증 |
| **F4** | `generation.py`, `hcx.py` 재사용, 실행 CLI | 기존 `LiveTransport`는 구현되어 있으나 합성 Mock만 검증. 동일 `ContextBuilder`→독립 Outbound check→`hcx.call_hcx`→schema/citation→독립 Output recheck. `LEGACY_READ_ONLY`는 외부 전송 승인이 아님. **합성 데이터만** 정확한 컨텍스트·명시적 허가·최대 1~2회·예산 상한으로 실호출 검증 옵션 제공. | Mock E2E PASS, opt-in 없는 0회, 승인된 합성 HCX 테스트는 사용자가 선택할 때만 PASS/FAIL |
| **F5** | `scripts/` 또는 `unified_v1/__main__.py`, 신규 평가 코드 | 하나의 CLI에서 전체 실험 실행. 프로파일/검색 정책/데이터 역할/실DB 모드를 설정으로 선택. 오류 구간별 상태·리포트. 기존 챗봇 진입점과 연결 여부 확인, 가능하면 별도 통합 V1 로컬 UI/모드로 질의 테스트. | Mac에서 복붙해 실행하는 명령으로 로컬 E2E·실험 비교·보고서 생성 가능 |

**P0 치명 회귀 점검:** `SnapshotGateAdapter`가 실제 V6와 같은 결과를 내는지 일부 합성 샘플뿐 아니라 과거 데이터에서 확인. `evidence_v2`에서 `ProtectionPlan.protection_status=CONFIRMED_HMAC`이 실제 HMAC 개체가 없는 안전 문서에도 나타날 수 있으니 상태명/집계 정의 분리. `existing index`가 `LEGACY_READ_ONLY`인 것은 질의/검색 수행 권한과 외부 전송 승인 권한을 혼동하지 말 것. `rank_documents`의 이름 lookup 후보가 실제 랭킹에서 누락되거나 잘못된 identity exact로 승격되지 않도록 테스트.

## 2. 실험 설계 — 베이스라인 확정용

### A. Stage 1 탐지·보호 (실모델)
- 같은 문서·같은 Gold·같은 정규화에서 `own2`, `quality3`, `quality4` 비교; 그다음 `team2`와 leave-one-out Ablation으로 KoELECTRA 실질 기여 측정. **순수 Raw NER 성능과 Gate 포함 파이프라인 성능을 구분**. `ax_klue` 등 V6 Gate 필수 신호 부재로 BLOCKED면 '모델 성능 저하'라고 표시 금지.
- 지표: Strict micro P/R/F1, 문자 보호율(전체/라벨별), 완전 미탐, FP/FN, READY/REVIEW/BLOCKED, HMAC 확정 비율, Mask-only 비율, 신규 모델 추가로 보호된 Span 수, 케이스별 오탐/누출, cold/warm p50/p95, 전체 RSS. 오류 케이스 PII 본문 로그 금지.
- 기존 171/187 테스트·Holdout 등 **이미 살펴본 자료는 개발/회귀 평가 전용**, 새로운 Blind라고 주장 금지. 골드 원본이 로컬에 없다면 실제 파일 경로/스키마부터 확인하고 임의 Gold 생성 금지. 품질 비교 시 수치와 분모를 반드시 명시.

### B. Stage 2 Retrieval (실DB + 실 KoE5)
- **비교 1:** 기존 187행 인덱스 그대로 → 검색 방법만 `own_v6/team_v3/hybrid_v1` 변경. 기존 88질의 같은 Gate/가명 쿼리, 같은 임베딩 설정. Hit@1/5, MRR, Recall@5, confusable@1/5, 실제 사용자 Top-5, READY 분모와 전체 88 분모를 모두 출력.
- **비교 2:** 새 Fusion 문서 텍스트가 실제로 바뀌는 경우에만 별도 인덱스 구축 → 동일 질의로 **전 파이프라인 효과** 측정. 원본 187행 인덱스 덮어쓰기/삭제/UPDATE/DDL 금지. 실제 준비 단계에서 REVIEW/차단 문서가 인덱스에서 빠지는 경우 coverage도 반드시 보고.
- 변별 실험: 동명이인, 비슷한 문서, 일부 식별자만 포함한 질의, 지역+인물, 순수 의미 질의, 기존 Q001/Q004/Q015/Q033/Q055 등 회귀 사례(해당 QID만을 위한 규칙 추가 금지). 단, QID별 지표를 학습/최적화에 사용했다면 최종 새 Blind는 별도 확보.

### C. HCX 답변 및 E2E
- HCX-007은 **모델과 키가 이미 있는 기존 `.env`/`hcx.py`를 재사용**. 구현 완료의 뜻은 호출 가능한 경로+가드+비용/명시적 opt-in가 있다는 것. 실제 키로 API 1~2회 호출은 사용자가 승인한 **합성 문서와 합성 질의에 대해서만** 수행. 그 이전까지 `NOT_RUN_BY_POLICY` 유지.
- 원문 187문서·88질의를 HCX에 자동 전송하지 말 것. 로컬 E2E는 실제 모델 탐지·가명처리·실 DB·실 KoE5 + Mock HCX로 먼저 완료. 사전에 승인된 안전한 데이터 범위에서만 외부 API 테스트.
- 최종 답변 평가: correct subject, claim-supported answer, cited doc accuracy, unsupported claims, PII leakage, CLARIFY 필요성, 응답 실패율. JSON Schema·citation ID 검증 ≠ 내용 사실성 증명.

## 3. 빠른 실행 순서 및 중단 조건

1. **모델 준비:** KoELECTRA 압축 해제 경로와 SHA 검사 → 4모델 독립 합성 예측 PASS. 실모델 실패 원인은 환경/매핑/디코딩별로 별도 기록, 가짜 모델로 대체 금지.
2. **안전 경로:** F1~F2 구현 및 합성/기존 회귀 PASS. HMAC 96-bit, Gate fail-closed, 불확실한 Identity와 마스킹 승인 유지.
3. **기존 DB 비교:** 187행 read-only → 88질의 Retriever 평가, 전후 fingerprint 확인. 88질의 원본/Gold 경로가 없으면 `DATASET_MISSING` 표시하고 평가기 구현만 완료.
4. **통합 데이터 경로:** 새 Fusion으로 문서 처리 → 격리 인덱스 → 질의 처리 → 검색 → Mock HCX → Output recheck E2E. 원본 데이터 사용·보존 정책 확인. 별도 인덱스가 준비되지 않았으면 **기존 DB 조회 결과를 신규 인덱스 결과라고 표시 금지**.
5. **합성 HCX 실호출(사용자 별도 승인 시)** → 발표 지표·샘플 UI 테스트 → `own2/quality3/quality4` 및 Retriever 기본값 선택. 안전성 기준을 통과한 프로파일 중에서만 품질·속도로 결정.
6. **main 병합 보류:** 실험·최종 검수 완료 전까지 `feature/unified-v1`에서 유지. 금지: 운영 DB 쓰기, 외부 비공개 PII 전송, 모델 가중치/`.env`/키 Git 추가, 테스트 기준 완화, 성능 주장 추정.

## 4. 결과물 — 문서 중복 금지

- `docs/unified_v1/FINAL_E2E_STATUS.md` **하나에만** 단계별 PASS/FAIL/BLOCKED/NOT_RUN, 실행 명령, 사용한 실제 모델 SHA·환경·프로파일, 평가 분모, 비교 표, 누락 자원, 발표 사용 가능/불가 주장 작성. 원문·개인정보·키/DSN 제외.
- `reports/unified_v1/` 또는 안전한 로컬 경로에 **PII 없는 요약 JSON/CSV**: 모델별 F1/coverage/gate, 검색 Hit/MRR, latency/RSS, HCX Mock/Live 분리. 원문/예측 span 단위 결과는 Git에 저장 금지.
- **복사 실행 가능한 CLI 명령**: `--check-models`, `--evaluate-step1`, `--evaluate-retrieval`, `--e2e-local`, `--synthetic-hcx-live` 등을 구현하거나 같은 의미의 옵션을 문서화. `--e2e-local`은 실제 로컬 PII 모델 + 실제 KoE5/DB + Mock HCX를 구분된 모드로 실행해야 함.
- 사용자가 발표 전에 이해할 수 있도록 **최종 권장 기본 모델 프로파일/검색 프로파일, 근거 수치, 이전 V6 대비 개선·악화, 아직 미검증 사항**을 간결하게 제시. 단 **정량 비교가 확보되기 전에는 기본값 강제 확정 금지**.

## 5. Codex 실행 지시 (이 문서 참조만 하면 됨)

> 현재 `feature/unified-v1`의 이 MD를 읽고 **F0→F5를 자율 구현·검증**해. KoELECTRA ft5는 `~/private-models/aegisrag/koelectra_ft5`로 전달될 것이므로 실물 검사와 원본 `hf_detector.py` 라벨 매핑부터 시작해. 4모델 실추론, Fusion/Mask·Gate, 이름 후보+Identity Sidecar, 기존 187행 DB 읽기 전용 검색/88질의, 신규 가명처리 인덱스의 격리 E2E, HCX-007 실호출 코드까지 완성해. 가능한 로컬 실모델·실DB 평가를 직접 실행하고, 미확보 데이터/격리 DB 등은 BLOCKED로 기록해. 실제 유료 HCX 호출은 **절대로 자동 실행하지 말고** 합성 데이터에 대한 명시적 허가를 요청해. `main`/원본 V6/HMAC/운영 DB/다른 worktree/팀원 원본 변경, 비밀키·가중치 Git 추가, 비공개 원문 외부 전송, Git push·PR·병합 금지. 단계별 PASS는 실측과 Mock을 구분하고, 중간 실패를 수정하되 안전한 후속 구현은 계속해. 결과는 `FINAL_E2E_STATUS.md`에 단일 보고하고 **F5에서 종료**해.
