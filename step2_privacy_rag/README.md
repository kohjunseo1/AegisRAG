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
  safe_e2e_contract_v2.py   Step1 → Gate → 가명처리 연결
  privacy_gate_v4.py        보호 누락 검토
  local_pseudonymizer_v2.py HMAC·주소 정규화
  region_keys_v1.py         부분 지역 검색 키
  prepare_v6_retrieval_v1.py  비공개 데이터 준비
  core.py                   실시간 질의 보호·임베딩·DB 검색
  reproduction.py           비파괴 적재·평가
  hcx.py                    전송 보조 검사·HCX 답변·인용 검증
  app.py                    로컬 HTTP API/웹 서버
  index.html, app.js, style.css 웹 UI
tests/                      합성·회귀 테스트
compose.yaml, init/         기존 팀 PostgreSQL/PGVector 초기 구성
```

공통 실행 CLI는 루트의 `scripts/aegisrag.py`입니다. 문서 적재 시 한 번 임베딩하고, 질문마다 새 질의만 임베딩합니다. DB의 기본 테이블은 `rag.aegis_koe5_v6_exp1`이며, 재현 테이블 사용 시 `AEGISRAG_TABLE`로 선택합니다.

## 5. 다음 개선 과제

현재 MVP는 로컬 단일 사용자·단일 턴 서비스입니다. **우선순위**는 근거/인용 정확도 실측, 단계별 지연시간과 매 질문 Step1 모델 재로딩 개선, 동명이인·REVIEW 10건 처리, 새 Blind 데이터 평가, 관리자 REVIEW UI 및 권한별 복원입니다. 서버 배포 시에는 인증·문서 접근 통제·DB 최소 권한·TLS·비밀키 관리가 추가로 필요합니다.

상세 실험 수치와 과거 정책 선택 과정은 [실험 이력](../docs/EXPERIMENT_HISTORY.md)에 보존했습니다.
