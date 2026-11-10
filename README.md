# AegisRAG

**Privacy-Preserving Retrieval-Augmented Generation for Financial Documents**

AegisRAG는 금융·상담 문서의 개인정보를 탐지·가명처리한 후, 가명 토큰과 의미 검색을 결합해 관련 문서를 찾고 근거 기반 답변을 생성하는 RAG 시스템입니다. 현재 저장소는 **Step1 탐지기 + Step2 V6 로컬 챗봇의 재현 가능한 기준 구현**입니다.

## 전체 처리 흐름

```text
[문서 적재: 최초/데이터 변경 시]
원문 문서 → Step1 A.X·Veil → Privacy Gate → HMAC·Region 가명처리
         → KoE5 문서 임베딩 → PostgreSQL/PGVector 저장

[챗봇: 질문마다]
질문 → Step1·Gate → 동일 HMAC·Region 처리 → KoE5 질의 임베딩
     → 기존 PGVector 검색 → HMAC·Region·Dense 재정렬(Top-5)
     → HCX-007 근거 선택/답변 → 인용 검증 → 웹 UI
```

REVIEW/BLOCKED 요청은 검색·LLM으로 진행하지 않습니다. 문서 임베딩은 **적재 시** 계산하고, 온라인에서는 **새 질문만** 임베딩합니다. 별도 Cross-Encoder 재랭커와 권한별 원문 복원은 현재 MVP 범위에 포함되지 않습니다.

| 구성 | 현재 기준 구현 |
|---|---|
| 개인정보 탐지 | A.X(6개 유형) + Veil(주소) |
| 가명처리 | Gate V4, HMAC V2, Region Key V1 |
| 검색 | KoE5 1024차원 + PostgreSQL/PGVector + 규칙 기반 재정렬 |
| 답변 | HCX-007, Structured Outputs, ANSWER/CLARIFY/INSUFFICIENT_EVIDENCE |
| 웹 UI | 로컬 단일 턴 챗봇 (`http://127.0.0.1:8768`) |

구현 상세: [Step1 탐지](step1_pii_detector/README.md) · [Step2 RAG](step2_privacy_rag/README.md).

## 빠른 실행

검증 기준은 **Python 3.11.13 / macOS x86_64 / CPU / Docker Desktop**입니다. 다른 OS·아키텍처도 실행할 수 있으나 동일 성능·속도는 별도 검증이 필요합니다. 비공개 저장소 Clone 권한, A.X·Veil Hugging Face 모델 읽기 권한과 개인 `HF_TOKEN`이 필요합니다.

```bash
git clone https://github.com/kohjunseo1/AegisRAG.git
cd AegisRAG
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e ./step1_pii_detector

test -f .env || cp .env.example .env
chmod 600 .env
```

`.env`에는 팀 공통 HMAC·DB 설정, 개인 `HF_TOKEN` 및 `LLM_API_KEY`를 설정합니다. **기존 `.env`를 덮어쓰거나 키를 Git에 올리지 마세요.**

```bash
python scripts/aegisrag.py models --device cpu
docker compose --env-file .env -f step2_privacy_rag/compose.yaml up -d db
python scripts/aegisrag.py init-db
python scripts/aegisrag.py smoke
```

- `models`: 실제 고정 모델의 로딩 확인(HF 권한 필요).
- `init-db`: 기존 DB 스키마의 **읽기 전용 점검**; 초기화/재적재 명령이 아닙니다.
- `smoke`: 공개 합성 데이터의 계약 테스트. **실제 Holdout 성능 검증이 아닙니다.**

### 챗봇 실행

기존 V6 문서 177개 테이블(기본 `rag.aegis_koe5_v6_exp1`)이 존재하고, 해당 데이터의 HCX 외부 전송 권한을 확인한 환경에서:

```bash
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/aegisrag.py run-chat --device cpu --port 8768
```

브라우저: **http://127.0.0.1:8768**. 이 서버는 로컬(127.0.0.1)에만 바인딩되며 **실행 중인 터미널 프로세스가 살아 있는 동안에만 동작**합니다. `Ctrl+C`로 종료합니다. Mac 재시작 후에는 Docker Desktop/DB와 챗봇을 다시 실행해야 할 수 있습니다. `docker compose up -d`로 실행한 DB 컨테이너와 웹 서버는 별도 프로세스입니다.

새 Clone에는 DB 데이터가 없습니다. 팀에서 승인된 비공개 입력을 준비해 **별도 테이블에 최초 적재**해야 챗봇을 실제 문서로 사용할 수 있습니다.

## 데이터 준비·평가 재현

비공개 문서 187건·질의 88건·Gold는 저장소에 포함하지 않습니다. 승인된 경로로 받은 뒤 `step2_privacy_rag/local_eval/`에 보관하고:

```bash
python scripts/aegisrag.py prepare-v6 \
  --gold-jsonl step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
  --out step2_privacy_rag/local_eval/reproduction/prepared.json

python scripts/aegisrag.py ingest-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json
```

두 번째 명령은 새로운 `rag.aegis_repro_<해시>` 테이블명을 **출력만** 합니다. 출력된 테이블명을 사용해 명시적으로 적재하고 평가합니다.

```bash
python scripts/aegisrag.py ingest-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json \
  --confirm '<출력된_테이블명>'

python scripts/aegisrag.py evaluate-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json --baseline
```

새 테이블로 채팅하려면 실행 명령 앞에 `AEGISRAG_TABLE='<출력된_테이블명>'`을 지정합니다. 기존 테이블을 삭제하거나 초기화하지 않습니다. **완전한 절차·데이터 계약·모델 revision·FAQ는 [재현 가이드](docs/REPRODUCIBILITY.md)**를 참고하세요.

### V6 검색 기준선

| 지표 | 결과 |
|---|---:|
| 문서 자동 처리 / REVIEW | 177 / 10 (전체 187) |
| 질의 READY / REVIEW / BLOCKED | 85 / 2 / 1 (전체 88) |
| 평가 가능 질의 | 81 |
| Hit@1 / Hit@5 | 73/81 (90.1%) / 79/81 (97.5%) |
| Recall@5 / MRR | 0.8802 / 0.9340 |

Recall@5는 **적재 가능한 Gold** 기준입니다(전체 Gold 기준 0.8791). 이 결과는 반복 분석한 Holdout에서 측정한 검색 기준선이며 PII 탐지 정확도, 미공개 데이터 성능 또는 LLM 답변 정확도를 뜻하지 않습니다.

## 테스트와 문서

```bash
PYTHONPATH=step1_pii_detector:step1_pii_detector/src \
python -m unittest discover -s step1_pii_detector/tests -v

PYTHONPATH=step2_privacy_rag/app \
python -m unittest discover -s step2_privacy_rag/tests -v

python scripts/aegisrag.py smoke
python scripts/scan_git.py --history
```

[재현 가이드](docs/REPRODUCIBILITY.md)에는 설치·모델/DB 교체·평가를, [실험 이력](docs/EXPERIMENT_HISTORY.md)에는 각 버전의 선택 근거와 최초 평가/사후 개선 결과를 기록했습니다.

**현재 범위:** 개인 로컬 MVP입니다. 사용자 인증, 문서별 접근 통제, Vault 복원, 운영용 출력 보호·관리자 REVIEW 워크플로, 원격 서버 배포는 후속 통합 단계에서 구현해야 합니다.
