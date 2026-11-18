# AegisRAG Unified V1 — Codex 후속 구현 명세 (간결본)

> **대상** `kohjunseo1/AegisRAG` / `feature/unified-v1` (검수 기준 HEAD `39581cb`)
> **목표** 기존 `unified_v1/` Mock 골격을 **실모델·실검색·HCX-007 실행 가능한 코드**로 완성. 기존 코드 재작성 금지.
> **범위** 실전 1·2: 탐지 → Fusion/Gate → HMAC → Identity → 검색 → HCX → 출력 검사. ACL·실명 복원은 제외.
> **고정** 기존 HMAC-SHA256 **24 hex(96 bit)** / 정규화 / 도메인 태그, KoE5 설정, 생성 모델 **HCX-007**.

## 1. 현재 상태와 주의점

| 부분 | 확인된 현재 상태 | 이번 작업 |
|---|---|---|
| A.X / Veil | 기존 pinned HF/ONNX 코드는 있음. 신규 통합 경로 실추론 미검증 | 기존 예측기 연결·원본 parity |
| KLUE-RoBERTa | 로컬 모델 4파일 확보; tokenizer PASS, 모델 ImportError | **격리 환경**에서 로딩·오프셋·예측 확인 |
| KoELECTRA ft5 | 체크포인트 없음 | `MISSING` 유지. 추후 어댑터 연결 |
| Fusion/Gate | 새 모델 후보 대부분 REVIEW; 기존 Gate에서 A.X/Veil 재추론 | 중복 제거·보호/확정 판정 분리 |
| Identity | 두 종류의 소유 속성 없으면 거의 UNRESOLVED | 이름 후보 검색과 동일인 확정 분리 |
| Retriever | InMemoryIndex + 가짜 Dense; 실 KoE5/DB 미연결 | 기존 DB 읽기 전용 연동 + 격리 인덱스 경로 |
| HCX | MockTransport만 허용; LiveTransport 없음 | 기존 `hcx.py` 연결 코드 구현, 실제 호출은 승인 후 |
| 검증 | Mock/합성 PASS, 실제 F1·검색·HCX 미측정 | 실제와 Mock 보고 엄격히 분리 |

**중요:** DB 미연결은 *반드시 재적재해야 해서*가 아니라 신규 Retriever에 실 DB 어댑터가 없기 때문. HCX도 *키가 없어서*가 아니라 신규 코드가 실호출을 막고 있기 때문. 기존 `.env`에 키가 있어도 자동 외부 전송은 금지.

## 2. 순차 구현 (각 단계 테스트 후 자율 진행)

### E0. 기존 동작 보존
- `main`, V6 기본 실행 경로, `rag.aegis_live_docs` **187행**, 기존 88질의 결과, HMAC 및 다른 worktree 보존.
- `own2`와 기존 V6의 Mask/Gate/HMAC 결과 parity 테스트; 현재 신규 테스트와 기존 회귀 테스트 재실행.
- 팀원 참조는 `../privacy-rag-chatbot-reference`의 커밋 `242543bf` (읽기 전용).

### E1. 실제 모델·프로파일·레이턴시
- `own2=ax+veil`, **`quality3=ax+veil+klue`**, `ax_klue`, `veil_klue`, `team2`, `quality4`, `drop_*`를 등록하되 **실행 가능한 조합만 `REAL_READY`** 표시. KoELECTRA 의존 조합은 `MISSING`.
- A.X/Veil은 기존 고정 predictor 재사용. KLUE는 `/Users/gojunseo/private-models/aegisrag/roberta_small_v1` 파일 해시 검증 후 **별도 모델 환경**에서 실행. 기존 `aegisrag` Conda/requirements 변경 금지.
- 팀원 모델 로딩 시 PyTorch/Transformers 충돌 해결, 15 BIO 라벨·오프셋·정확한 디코딩·팀원 원본 예측 차이 검증. 실제 parity 불가 시 `BLOCKED` 기록.
- 요청별 subprocess+모델 재로딩 제거: **상주 Worker/재사용 실행기**, 타임아웃·동시 요청·프로세스 오류 fail-closed. 모델별 cold load, warm p50/p95, peak RAM 계측.
- **같은 요청의 Raw 예측을 Gate와 Fusion에서 재사용**. `team2`가 몰래 A.X/Veil을 실행하면 안 됨. 기존 Gate 안전 규칙은 동등한 신호가 확보될 때만 해당 프로파일을 승인.

### E2. Evidence Fusion·Gate
- 기존 보호 영역 **절대 축소 금지**. 모델/규칙 후보의 출처·오프셋·충돌 사유 보존.
- 보호 필요 여부와 Typed HMAC 확정 가능 여부를 별도 판단: `CONFIRMED_HMAC`, `LOCAL_MASK_ONLY_PENDING_APPROVAL`, `REVIEW`, `BLOCKED`.
- 단순 신규 후보를 무조건 REVIEW로 보내지 않되, **안전한 Mask-Only ≠ 자동 적재/외부 전송 허가**. 불확실 경계·검사 실패는 REVIEW/BLOCKED.
- 성씨·복성·띄어쓰기·조사·주소 변형과 일반어 오탐 반례 추가. 특정 이름/질의 ID 하드코딩 금지.
- 동일 후보에 대한 old/new: Strict F1, 문자 보호율, 완전 미탐, FP, READY/REVIEW/BLOCKED, HMAC 확정률 비교 도구 마련.

### E3. 동명이인·HMAC
- `NAME_LOOKUP_KEY`(후보 검색), `OWNED_ATTRIBUTE_KEY`, `PERSON_ENTITY_KEY`(검증된 동일인), `MENTION_ID` 분리.
- 이름만 있는 질의도 **후보 검색 가능**하게 하되, 이름 일치만으로 동일인 확정 금지. 답변 대상이 실제로 모호할 때만 CLARIFY.
- 연락처 공유/변경·속성 누락·동일 문서 다른 사람/문서 간 같은 사람 테스트. DB/쿼리에 Entity ID 전달.
- 원래 `make_hmac_token`과 canonicalize를 재사용. 기존 24-hex 토큰 변경 금지.

### E4. Retriever — **기존 DB 우선 재사용**
- 기존 `rag.aegis_live_docs`와 KoE5를 **읽기 전용**으로 연결하는 어댑터부터 작성. 모델 pin·1024차원·query/passage prefix·key_tag·테이블 스키마를 검증하고 조건이 맞으면 **재적재 없이** `own_v6/team_v3/hybrid_v1` 검색 비교.
- 기존 인덱스의 가명 문서·HMAC/Region 토큰·임베딩을 사용하며 동일 검색 기준으로 Hit@1/5, MRR, Recall@5, confusable, 전체/READY 분모 및 **사용자에게 보여주는 실제 Top-5** 계산.
- 통합 Fusion으로 가명 본문이 달라지거나 Entity/청크 스키마를 실험할 때만 **별도 격리 DB/인덱스** 생성. 운영 테이블 UPDATE/DELETE/DDL/재적재 절대 금지. pgvector `vector(1024)`+GIN 등 실제 SQL 구현·테스트; 미확보 시 `DB_REAL_NOT_RUN`.
- Token ALL/ANY + Region으로 후보 수집, KoE5와 identity constraint로 재정렬. 검색 프로파일별 같은 문서/질의/모델·정규화 사용. RRF/청크는 **선택 옵션**, 기본 구현 검증 뒤 실험.

### E5. HCX-007·출력 재검사
- 기존 `step2_privacy_rag/app/hcx.py`의 HCX-007 요청·JSON Schema·타임아웃·오류/인용 검증을 **재사용하는** `LiveTransport` 작성. Mock 유지. 중복 클라이언트 구현 지양.
- 기존 `.env`를 **읽기 전용으로 참조할 수 있는 경로 설정** 추가. 키를 복사/로그/커밋하지 않음.
- `Outbound Privacy Check → HCX-007 → Output Recheck`를 실제 실행 가능한 코드로 연결. 독립 검사기 오류/미보호 토큰/잘못된 citation은 차단.
- **코드 구현·Mock/로컬 합성 검증은 허용**, 키가 있어도 실제 API 호출은 기본 OFF. 명시적 opt-in + 합성 데이터 + 호출/비용 제한이 있어야 실호출. 기존 비공개 Holdout/Blind 문서 외부 전송 금지.

## 3. 테스트·완료 판정

- 각 단계에서 신규·기존 unittest, synthetic Mock E2E, 실패/예외/동시 요청/키 불일치·유출 회귀 실행. 테스트 기대값 완화 금지.
- 가능한 경우 실제 **로컬 3모델 합성 smoke** 및 DB **읽기 전용 검색 smoke** 실행; 모델/DB 미접근은 `BLOCKED`로 구분. 실제 HCX 호출은 금지.
- 동일한 입력·평가셋과 Gold를 유지한 모델 조합/Ablation 수행. 실측 F1·latency·Hit/MRR와 Mock 수치를 절대 혼합하지 않음.
- 결과는 **`docs/unified_v1/FOLLOWUP_STATUS.md` 한 파일**에만 기록: 단계별 DONE/BLOCKED, 변경 파일, 실행 명령, 실제/Mock/미측정, 테스트 결과, 다음 필요한 입력. 불필요한 중복 보고서 생성 금지.
- **E5 코드·안전한 로컬 검증까지만 자율 수행하고 중단**. 새로운 Blind, 비공개 원문 외부 전송, 실제 HCX 유료 호출, 운영 DB 쓰기, GitHub push/PR/`main` 병합은 별도 승인.

## 4. Codex에 붙여넣을 지시문

```text
현재 AegisRAG feature/unified-v1 worktree에서 작업해.

문서: docs/unified_v1/AegisRAG_Unified_V1_Codex_Followup_Compact.md

이 문서를 읽고 E0→E5를 순차적으로 자율 구현·테스트해. 이전 unified_v1 코드는 재사용하고 필요한 부분만 수정해. 각 단계 테스트가 실패하면 수정하고, 실제 리소스가 없어 검증할 수 없는 항목은 BLOCKED로 기록하고 가능한 다음 코드 작업을 계속해.

핵심: quality3(AX+Veil+KLUE) 실모델 연결, 중복 추론 제거, Fusion/Mask-Only·REVIEW 구분, 이름 후보 검색과 Identity 확정 분리, 기존 187행 DB 읽기 전용 Retriever 연동(필요 없는 재적재 금지), 기존 hcx.py를 재사용한 HCX-007 LiveTransport 코드 작성(실호출 금지).

기존 main/V6/운영 DB/비밀키/다른 worktree 변경 금지. KoELECTRA는 MISSING, 임의 대체 금지. 기존 Python 환경 업그레이드 금지. 팀원 코드는 읽기 전용. 실제 HCX 호출, 운영 DB 쓰기, private corpus 외부 전송, GitHub push/병합 금지.

결과는 docs/unified_v1/FOLLOWUP_STATUS.md 단일 파일로 기록하고 E5에서 중단해. 실모델/실DB/Mock 성공 여부는 명확히 구분해.
```
