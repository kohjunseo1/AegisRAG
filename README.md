# AegisRAG

금융 문서를 로컬에서 개인정보 탐지·가명처리한 뒤 검색하고, 근거 문서를 인용하는 V6 기준 구현입니다. Step1 A.X/Veil → Gate → HMAC/Region → KoE5/PGVector → HCX-007 순서로 실행합니다. REVIEW/BLOCKED는 검색·LLM으로 진행하지 않습니다. 각 질문은 독립 처리합니다.

기존 팀 HMAC 키, PostgreSQL 계정·포트·DB명, 스타터 `compose.yaml`·`init/`, 루트 `.env`는 그대로 사용합니다. **기존 테이블 초기화는 없습니다.** 팀원마다 DB 내용이 달라도 같은 비공개 문서·키·모델·코드·설정으로 별도 재현 테이블을 만들 수 있습니다.

## Quick Start

검증 환경: macOS x86_64, Python 3.11.13, CPU, Docker Desktop의 PostgreSQL 16 + pgvector. Apple Silicon/MPS·Linux는 별도 검증 대상입니다. Private HF 모델 두 곳의 읽기 권한과 개인 `HF_TOKEN`이 필요합니다.

```bash
git clone https://github.com/kohjunseo1/AegisRAG.git
cd AegisRAG
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e ./step1_pii_detector
```

Python 3.11.13 인터프리터를 준비한 뒤 위 명령을 사용합니다. 잠금 패키지는 별도 깨끗한 Python 3.11.13 venv에서 설치·`pip check` 검증했습니다. `environment.yml`도 동일 Python/잠금 목록/Step1 설치 순서를 선언한 Conda용 설정으로 제공합니다.

기존 `.env`가 있으면 보존합니다. 처음 설치하는 팀원만 다음 명령으로 템플릿을 만든 뒤, 팀에서 제공한 기존 DB/HMAC 설정과 개인 HF/API 키를 넣습니다. 키를 터미널 명령에 직접 적지 마세요.

```bash
test -f .env || cp .env.example .env
chmod 600 .env
python scripts/aegisrag.py models --device cpu
docker compose --env-file .env -f step2_privacy_rag/compose.yaml up -d db
python scripts/aegisrag.py init-db
python scripts/aegisrag.py smoke
```

`init-db`는 읽기 전용 사전 점검입니다. 스타터 스키마는 Docker가 **새 볼륨 최초 시작 때만** 만듭니다. 기존 볼륨에서 초기화 스크립트를 다시 실행하지 않습니다. 실제 모델·DB를 포함한 공개 합성 적재 검증은 [재현 문서](docs/REPRODUCIBILITY.md#공개-합성-검증)를 따릅니다. HF 접근·비공개 데이터가 없는 경우 합성 계약 테스트만 실행할 수 있으며, Holdout 성능 재현으로 간주하지 않습니다.

팀에서 비공개 187문서·88질의·Gold를 `step2_privacy_rag/local_eval/`로 안전하게 전달받은 다음 실행합니다.

```bash
python scripts/aegisrag.py prepare-v6 \
  --gold-jsonl step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
  --out step2_privacy_rag/local_eval/reproduction/prepared.json
python scripts/aegisrag.py ingest-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json
```

위 적재 명령은 먼저 `rag.aegis_repro_<해시>` 대상과 문서 수를 출력합니다. 출력된 **정확한 테이블명**을 `--confirm`으로 전달하면 새 테이블에 적재합니다. 다음 명령의 `<출력된_테이블명>`을 그 값으로 바꾸세요. 같은 입력의 재실행은 기존 행을 검증하며, 다른 내용이 있으면 실패합니다.

```bash
python scripts/aegisrag.py ingest-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json \
  --confirm '<출력된_테이블명>'
python scripts/aegisrag.py evaluate-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json --baseline
AEGISRAG_TABLE='<출력된_테이블명>' \
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/aegisrag.py run-chat --device cpu --port 8768
```

챗봇: **http://127.0.0.1:8768**. 마지막 명령의 전송 허용은 해당 데이터의 외부 HCX 전송 권한을 확인한 운영자만 설정합니다. 합성 검증은 HCX mock으로도 완료할 수 있습니다. 운영 Holdout의 실제 외부 전송은 이 작업에서 수행하지 않습니다.

기존 177문서 V6 인덱스를 그대로 읽으려면 `AEGISRAG_TABLE`을 생략합니다. 질문할 때는 질의만 임베딩하며, 문서 임베딩은 적재 때만 수행합니다.

## 검증과 구조

```bash
PYTHONPATH=step1_pii_detector:step1_pii_detector/src \
python -m unittest discover -s step1_pii_detector/tests -v
PYTHONPATH=step2_privacy_rag/app \
python -m unittest discover -s step2_privacy_rag/tests -v
python scripts/aegisrag.py smoke
python scripts/scan_git.py --history
```

```text
config/model_manifest.json       # KoE5 revision, HCX 설정
step1_pii_detector/              # 동결 탐지 패키지·평가·핵심 테스트
step2_privacy_rag/app/           # 단일 앱과 실제 V6 정책 의존성
step2_privacy_rag/tests/         # 합성 회귀·적재 보호 테스트
step2_privacy_rag/compose.yaml   # 기존 스타터 그대로
step2_privacy_rag/init/          # 기존 최초 볼륨 초기화 그대로
scripts/aegisrag.py              # 모델·준비·적재·평가·챗봇 공통 CLI
docs/REPRODUCIBILITY.md          # 데이터·DB·모델 교체·FAQ
docs/EXPERIMENT_HISTORY.md       # 과거 실험과 이번 검증 증거
```

V6 기준선은 177 LOCAL_PREPARED/10 REVIEW, 질의 85 READY/2 REVIEW/1 BLOCKED, 평가 가능 81개에서 Hit@1 73/81, Hit@5 79/81, Recall@5 0.8802, MRR 0.9340입니다. 평가 시 중립 문서를 제외하는 기존 채점식을 유지합니다. **177/187은 처리율이며 탐지 정확도가 아닙니다.** Holdout 사후 분석이 포함된 기준선으로, 새 데이터 일반화나 생성 답변 정확도를 뜻하지 않습니다.

## 향후 서버 운영 설계 메모

현재는 localhost 단일 사용자 기준입니다. 통합 시 인증된 사용자·테넌트 권한을 SQL 후보 선정에 적용하고 Vault 복원 권한을 분리해야 합니다. 원격 DB TLS/인증, 최소 권한 계정, 백업·마이그레이션, SSH 운영, 시크릿 저장소를 준비해야 합니다. 실제 클라우드 배포나 공개 전환은 포함하지 않습니다.
