# Step2 — Privacy-Preserving RAG (V6)

Step2는 Step1의 탐지 결과를 **검증·가명처리**하고 PostgreSQL/PGVector에 저장해, 자연어 질문에 대한 근거 기반 답변을 생성합니다. 현재 코드는 **로컬 단일 턴 챗봇 MVP**입니다.

## 1. 전체 구조

```text
[문서 전처리·적재]
원문 → Step1 A.X/Veil → Privacy Gate V4
     → 이름 경계·non-linked 정책 → HMAC V2 + Region Key V1
     → KoE5 passage embedding → PGVector

[질의·응답]
질문 → 동일 Step1/Gate/HMAC·Region 처리
     → KoE5 query embedding → PGVector Dense 순위
     → HMAC 일치 수 → Region 일치 수 → Dense 순위(Top-5)
     → 잔여 패턴 송신 검사 → HCX-007 근거 선택·답변 → Citation 검증 → UI
```

- **Privacy Gate:** `REVIEW_REQUIRED` / `BLOCKED`는 자동 검색·외부 전송에서 제외합니다.
- **HMAC:** 정규화된 개체와 팀 공통 비밀키로 결정적 토큰을 생성합니다. 같은 키·정규화 규칙일 때 일치 검색이 가능합니다.
- **Region Key:** 주소 전체 토큰만으로는 찾기 어려운 부분 지역 질의를 보완하는 계층적 검색 키입니다.
- **검색:** KoE5 1024차원 임베딩과 규칙 기반 재정렬을 사용합니다. **Cross-Encoder Reranker는 최종 온라인 경로에 없습니다.**
- **HCX-007:** Top-5 후보를 읽되 실제 관련 있는 근거만 인용합니다. 출력 상태는 `ANSWER`, `CLARIFY`, `INSUFFICIENT_EVIDENCE`입니다.
- **보호:** 기존 HMAC 토큰을 유지하고, 송신 직전 확인된 전화·이메일·번호 패턴은 보조 마스킹합니다. 이는 모든 미탐 개인정보의 부재를 인증하지 않습니다. 인용 ID 검증도 답변의 사실성 검증을 완전히 대체하지 않습니다.

## 2. 평가 기준선

| 항목 | V6 결과 |
|---|---:|
| 입력 문서 | 187 |
| LOCAL_PREPARED / REVIEW | 177 / 10 |
| 질의 READY / REVIEW / BLOCKED | 85 / 2 / 1 |
| 평가 가능 질의 | 81 |
| Hit@1 | 73/81 (90.1%) |
| Hit@5 | 79/81 (97.5%) |
| Recall@5 (적재 가능 Gold) | 0.8802 |
| Recall@5 (전체 Gold) | 0.8791 |
| MRR | 0.9340 |

Hit@5는 Top-5에 정답 문서가 **하나 이상** 있는 질의 비율이고, Recall@5는 여러 Gold 문서의 회수 비율을 뜻합니다. Q059는 Top-5 집합은 같지만 4·5위의 동점성 순서 차이가 있습니다. 기존 Holdout을 반복 분석한 개발 기준선이며, 새 데이터에서의 일반화나 LLM 답변 정확도를 보장하지 않습니다.

## 3. 실행

의존성 설치, HF 모델 권한, `.env` 구성은 [루트 README](../README.md)에서 진행합니다.

```bash
docker compose --env-file .env -f step2_privacy_rag/compose.yaml up -d db
python scripts/aegisrag.py init-db
python scripts/aegisrag.py smoke
```

`smoke`는 합성 계약 점검이며 실제 문서 검색 성능을 검증하지 않습니다. 기존 V6 177문서 인덱스가 있다면 다음으로 챗봇을 시작합니다.

```bash
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/aegisrag.py run-chat --device cpu --port 8768
```

접속: **http://127.0.0.1:8768**. 시작 옵션은 해당 데이터의 HCX 전송 권한 확인 후 사용합니다. **웹 서버는 상시 서비스가 아닙니다.** 터미널에서 실행 중일 때만 제공하고 `Ctrl+C`로 종료합니다. Docker DB는 별도의 컨테이너이므로 필요하면 `docker compose ... up -d db`로 다시 시작합니다.

DB 인덱스가 없다면 **비공개 원문·질의·Gold를 별도 안전 경로로 제공받아** 다음 단계로 재현합니다.

```bash
python scripts/aegisrag.py prepare-v6 \
  --gold-jsonl step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
  --out step2_privacy_rag/local_eval/reproduction/prepared.json
python scripts/aegisrag.py ingest-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json
```

적재는 출력된 새 테이블명으로 `--confirm '<테이블명>'`을 다시 지정해야 수행되며 **기존 테이블을 덮어쓰지 않습니다.** 평가, 모델 revision, 인증 설정과 비공개 데이터 공급에 대한 절차는 [재현 가이드](../docs/REPRODUCIBILITY.md)에 있습니다.

## 4. 코드 구성

```text
app/
  document_preparation.py     Step1 → Gate → 가명처리 연결
  privacy_gate.py             개인정보 보호 검토
  gate_validation.py          보호 구간·탐지 결과 검증
  name_boundary_policy.py     이름 경계 후보 제안
  name_protection_policy.py   이름 보호 정책 결정
  name_masking_strategies.py  이름 마스킹 전략·평가 보조
  name_protection_handoff.py  이름 보호와 동일인 연결 분리
  entity_pseudonymizer.py     기본 개체 정규화·HMAC 처리
  local_pseudonymizer.py      주소 정규화를 포함한 로컬 가명처리
  region_keys.py              부분 지역 검색 키
  dataset_preparation.py      비공개 데이터 준비
  core.py                     실시간 질의 보호·임베딩·DB 검색
  reproduction.py             비파괴 적재·평가
  hcx.py                      전송 보조 검사·HCX 답변·인용 검증
  app.py                      로컬 HTTP API/웹 서버
  index.html, app.js, style.css 웹 UI
tests/                        합성·회귀 테스트
compose.yaml, init/           기존 팀 PostgreSQL/PGVector 초기 구성
```

공통 실행 CLI는 루트의 `scripts/aegisrag.py`입니다. 문서 적재 시 한 번 임베딩하고, 질문마다 새 질의만 임베딩합니다. DB의 기본 테이블은 `rag.aegis_koe5_v6_exp1`이며, 재현 테이블 사용 시 `AEGISRAG_TABLE`로 선택합니다.

### DB 오류 진단 및 한글 입력

`V6_DB_READ_ERROR`는 DB 접속·권한·테이블·SQL 실행 오류를 뭉뚱그렸던 코드입니다. 이 수정본은 `V6_DB_TABLE_NOT_FOUND`, `V6_DB_ACCESS_DENIED`, `V6_DB_AUTH_FAILED`, `V6_DB_CONNECTION_FAILED`, `V6_DB_DATABASE_NOT_FOUND`, `V6_DB_SCHEMA_MISMATCH`, `V6_DB_QUERY_FAILED`로 구분합니다.

실행 중인 챗봇과 **같은 루트 .env 및 환경변수**에서 아래 명령으로 안전한 읽기 전용 진단을 합니다. DB 컨테이너를 초기화하거나 기존 177문서 테이블을 수정하지 않습니다.

```bash
python scripts/diagnose_v6_db.py
```

현재 활성화된 DB 테이블과 행 수만 출력하고 비밀번호, HMAC 토큰, 원문 문서는 출력하지 않습니다. 로컬 서버 재시작 후 `/api/index-status`의 오류 코드를 확인하세요.

한글을 조합하는 동안 Enter를 누르면 첫 Enter는 글자 조합만 끝내고, **두 번째 Enter에서 전송**하도록 했습니다. 전송 버튼을 눌러도 조합이 완료된 후 질문을 전송합니다.

## 5. 단일 운영 인덱스 (증분 적재)

기존 `ingest-v6`는 재현 가능한 실험 스냅샷을 생성하는 명령으로 유지합니다. **운영에서는 한 테이블에 누적 적재**합니다. 이미 검증된 187문서 스냅샷은 복사하지 않고 단 한 번 물리적으로 `rag.aegis_live_docs`로 이름을 변경해 승격할 수 있습니다. 실행하기 전에 **8768 챗봇을 종료**하고 `.env`의 기존 팀 설정을 백업하세요. 관리자 REVIEW 서비스는 현재 공개 저장소 코드와 별도이므로, 검토 승인 결과를 새 승인 배치 형식으로 넘기는 작업은 통합이 필요합니다.

```bash
python scripts/aegisrag.py promote-live \
  --source rag.aegis_repro_11d26bb4db0f23360bdb \
  --confirm PROMOTE_TO_rag.aegis_live_docs
```

이 명령은 **기존 테이블에 대한 ALTER TABLE RENAME 한 번만 수행**합니다. 승인된 기존 187건을 복사하거나 다시 임베딩하지 않고, 기존 177건이 아닌 현재 187건을 검증한 후 실행합니다. 성공 시 `.env`를 다음과 같이 변경합니다.

```text
AEGISRAG_TABLE=rag.aegis_live_docs
AEGISRAG_INDEX_MODE=live
AEGISRAG_LIVE_MIN_ROWS=187
```

서버 재시작 시 `index_mode=live`는 행 수가 **187개 이상**이고 모든 행의 HMAC 키 버전이 같을 때 조회를 허용합니다. 신규 문서가 추가되어 188, 189개가 되어도 `AEGISRAG_EXPECTED_ROWS=187` 때문에 차단되지 않습니다. 조회는 REPEATABLE READ로 일관된 스냅샷을 사용합니다.

**새 문서 증분 적재:** 원본이 아니라 검토·가명처리·보호 재검증을 마친 승인 문서만 로컬 파일로 전달합니다. 아래 JSONL이 아닌 JSON 형식이며 `hmac_key_tag`는 실제 환경에서 계산한 비밀 아닌 키 버전 식별자여야 합니다. 민감한 원문 또는 비밀번호를 넣지 않습니다.

```json
{
  "format": "aegisrag-live-approved-v1",
  "hmac_key_tag": "<key_tag_from_local_approval_pipeline>",
  "documents": [
    {
      "doc_id": "S001",
      "text": "합성 교육은 수요일입니다.",
      "region_keys": [],
      "approval_state": "AUTO_READY"
    }
  ]
}
```

```bash
python scripts/aegisrag.py ingest-live \
  --input /path/to/locally_approved_documents.json \
  --confirm rag.aegis_live_docs
```

`AUTO_READY`와 `HUMAN_APPROVED`는 **신뢰 가능한 로컬 승인 워크플로가 발행한 값이어야 하며, 필드만으로 사람의 실제 승인을 증명하지는 않습니다.** `REVIEW_PENDING`은 적재 불가능합니다. 추가 문서의 `doc_id`가 새 것이면 INSERT, 기존 것이고 텍스트/Region 토큰이 바뀌면 해당 행만 UPDATE+재임베딩합니다. 변경이 없으면 SKIP하므로 모델을 다시 로드할 필요도 없습니다. 정식 관리자 검토 UI와 이 로컬 승인 배치의 연결은 별도로 검증해야 합니다.

테이블 삭제, `DROP SCHEMA`, 원문·Gold 저장, 다른 문서 재임베딩은 하지 않습니다. 기존 개인정보 보호 및 외부 LLM 전송 승인 조건은 그대로 유지됩니다.

## 6. 로컬 E2E 통합 진단 · 88질의 평가

서버와 실제 DB·API는 사용자의 Mac에서만 실행됩니다. GitHub CI는 개인정보 없는 합성 계약 테스트입니다.

```bash
# 1) 기존 로컬 DB 메타데이터 점검 (읽기 전용)
python scripts/verify_v6_e2e.py

# 2) 강제로 REVIEW → (모의 관리자) 승인 계약을 검사. DB 변경 없음
python scripts/verify_v6_e2e.py --synthetic-review

# 추가: 실제 A.X/Veil/Privacy Gate에 가상의 REVIEW 후보 문서를 입력해 로컬 상태만 검증
python scripts/verify_v6_e2e.py --real-review-probes

# 3) 실제 질문의 Step1+HMAC+PGVector Top-5를 로컬에서 확인. HCX 미호출
python scripts/verify_v6_e2e.py --interactive

# 4) 승인받은 데이터에서만 HCX 직접 호출. 답변·인용 확인
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
  python scripts/verify_v6_e2e.py --interactive --transport hcx

# 5) 이미 실행 중인 실제 웹챗의 /api/chat 경로 전체 테스트
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
  python scripts/verify_v6_e2e.py --interactive --transport http

# 6) 보안이 유지된 로컬 평가 산출물로 검색 지표와 CPU retrieval latency 평가
python scripts/verify_v6_e2e.py \
  --eval-artifact step2_privacy_rag/local_eval/reproduction/prepared.json
```

질문 입력 예: `오가은`, `오가은 이름 있는 자료`, `윤선영 정보`는 `--interactive` 모드에서만 로컬로 입력합니다. GitHub 코드·일반 로그에 비공개 질문/문서 본문을 올리지 마세요. `--transport local`은 문서 ID와 매칭 수, 지연시간만 출력합니다. `--transport hcx/http`는 외부 전송을 명시적으로 승인한 경우에만 실행하세요.

평가에서 **Hit@1/5, Recall@5, MRR**은 기존 평가 방식에 맞춰 neutral 문서를 *채점 시* 제외하고, **Confusable@1/5**는 실제 챗봇에 전달되는 neutral 미제외 Top-5 기준으로 계산합니다. 따라서 Confusable@5는 낮을수록 좋으며, 높은 Hit@5라도 동명이인/혼동 문서가 함께 나올 수 있습니다. `eligible`(정답이 인덱스에 있고 준비된 질문), `deferred`, `no_indexable_gold`를 분리해 분모를 명시합니다. 지연시간은 워밍업 후 로컬 임베딩+DB+리랭크 기준으로 측정하며 Step1과 HCX 시간은 별도입니다.

`--synthetic-review`는 Mock 모델의 `REVIEW_REQUIRED` 동작과 승인을 흉내 낸 데이터 계약을 검증합니다. **8771 관리자 웹 UI의 실제 승인·재검증·삽입 동작까지 검증했다는 뜻은 아닙니다.** 실제 UI는 로컬 전용이므로 해당 코드와 연결하려면 별도 통합 테스트가 필요합니다.

### STEP1_GATE_FAILED 오류 확인 (Mac 실모델, 안전 진단)

`STEP1_GATE_FAILED`가 첫 질의에 발생한 다음 `STEP1_DETECTOR_FAILED_RESTART_REQUIRED`가 반복된다면 해당 프로세스는 이미 fail-closed 상태입니다. 같은 프로세스에서 계속 재시도하지 마세요. `aegisrag-retrieval` 등 **현재 실행 중인 Python 환경**에서 다음을 실행합니다.

```bash
python scripts/diagnose_step1.py
```

단계별로 `STEP1_ENVIRONMENT`, `STEP1_MODEL_INIT`, `STEP1_BACKEND`, `STEP1_DETECT`, `STEP2_LOCAL_PREPARE`를 출력합니다. 에러의 **유형과 저장소 내부 위치**만 출력하며 원문, 개인정보, HMAC, API 키, 스택 내용이나 외부 의존성의 에러 메시지를 출력하지 않습니다. 합성 문장만 사용하며 DB나 HCX에는 접근하지 않습니다. 먼저 해당 환경에서 `python scripts/aegisrag.py models --device cpu`를 재실행해도 좋습니다.

`STEP1_MODEL_INIT`에서 실패하면 HF_TOKEN 유무·해당 환경의 의존성·모델 revision 권한부터 확인하고, 초기화가 통과한 다음 백엔드가 실패하면 A.X/Veil 런타임으로 범위를 좁힙니다. `STEP2_LOCAL_PREPARE`만 실패한다면 모델은 동작하며 게이트/가명처리 연동을 확인해야 합니다. 이전에 다른 Conda 환경에서 나온 `PINNED_MODELS_READY`만으로 현재 환경의 성공을 가정하지 마세요.

## 7. 다음 개선 과제

현재 MVP는 로컬 단일 사용자·단일 턴 서비스입니다. **우선순위**는 근거/인용 정확도 실측, 단계별 지연시간과 매 질문 Step1 모델 재로딩 개선, 동명이인·REVIEW 10건 처리, 새 Blind 데이터 평가, 관리자 REVIEW UI 및 권한별 복원입니다. 서버 배포 시에는 인증·문서 접근 통제·DB 최소 권한·TLS·비밀키 관리가 추가로 필요합니다.

상세 실험 수치와 과거 정책 선택 과정은 [실험 이력](../docs/EXPERIMENT_HISTORY.md)에 보존했습니다.
