# V6 재현 절차

## 보존되는 팀 설정

루트 `.env`, `step2_privacy_rag/compose.yaml`, `init/01_schema.sh`를 변경하지 않습니다. `.env.example`은 새 설치용 빈 비밀값 템플릿입니다. 팀 비밀키·계정을 교체하거나 공유 토큰을 생성하지 않습니다. 기존 볼륨·`rag.chunks`·`vault.token_map`·`rag.aegis_koe5_v6_exp1`을 초기화하지 않습니다. `docker compose down -v`를 재현 절차에 사용하지 않습니다.

프로세스 환경변수가 `.env`보다 우선합니다. DB 기본값은 기존 스타터의 `localhost:5433`, `pii_rag`, `rag_ingest`이며 실제 `.env` 값을 우선 사용합니다. `DB_HOST`, `DB_PORT`, `POSTGRES_DB`, `DB_USER`, `RAG_PASSWORD`, 선택 `DB_SSLMODE`로 연결을 바꿀 수 있습니다. 원격 연결은 미래 이식용 설정이며 이번 검증은 localhost뿐입니다. 읽기 SQL은 트랜잭션 READ ONLY를 강제합니다. `DB_USER=rag_readonly` 사용 시 해당 계정 비밀번호를 프로세스의 `RAG_PASSWORD`로 안전하게 공급합니다. 팀 `.env`의 값을 덮어쓰지 마세요.

`AEGISRAG_TABLE`은 `schema.table` 형태의 식별자이고 `AEGISRAG_EXPECTED_ROWS` 기본값은 177입니다. 채팅은 테이블 행 수·키 tag를 검증합니다. 적재는 일반 테이블을 받지 않으며 오직 입력·코드·정책·모델 설정 해시로 산출한 `rag.aegis_repro_<20자리 해시>`만 생성합니다. 대상 확인 전에는 DB 쓰기·문서 임베딩이 없습니다. 적재·권한 부여·메타데이터 기록은 한 트랜잭션에서 처리하고, 동일 입력은 모든 행·벡터를 검증합니다. 충돌 시 덮어쓰기 없이 실패합니다. 별도 스키마가 필요하면 관리자가 먼저 격리 DB에 동일 스타터를 준비합니다.

## 고정 모델과 환경

| 모델 | 저장소 | 고정 revision |
|---|---|---|
| A.X | `junseokoh/pii-ax-mixed-v1` | `c0f99b1dc113befefeff1a3091087d50bc3ea635` |
| Veil | `junseokoh/pii-veil-mixed-v1` | `e6e7d20fa9baa43a938f5f6cc780d3f2c9650fb9` |
| KoE5 | `nlpai-lab/KoE5` | `237b580aea358e3042fcf5baf78124c33c3afd16` |

A.X/Veil은 Step1 manifest와 기존 HF 로더를 유지합니다. 모델 소유자가 팀원 계정에 private 읽기 권한을 부여하고 각자가 개인 `HF_TOKEN`을 사용합니다. 모델은 공개 전환하지 않습니다. `python scripts/aegisrag.py models`는 다운로드·로딩·짧은 합성 탐지를 실제 수행합니다. HF 웹사이트 로그인만으로 Python 프로세스의 토큰 설정을 대체하지 않습니다.

KoE5는 1024차원, 최대 512토큰, 문서 `passage: `, 질의 `query: ` 접두사, 정규화 float32, 적재 batch 4, 온라인 질의 batch 1입니다. 코사인 거리·doc_id 순위에 HMAC 정확 일치 개수 → Region 일치 개수 → Dense 순위를 적용합니다. Gold는 검색에 관여하지 않습니다. HCX는 기존 endpoint, `HCX-007`, temperature 0, reasoning_effort none, max_completion_tokens 1024, stream false, JSON Schema를 고정합니다. 문서당 최대 1,500자 발췌, 총 문맥 8,500자 제한을 유지합니다.

기준 OS/CPU는 macOS x86_64/Python 3.11.13/CPU입니다. `requirements.lock`은 실제 환경의 pip 패키지 버전을 고정하며 깨끗한 venv 설치와 `pip check`를 검증했습니다. 기본 설치는 검증한 venv 절차입니다. `environment.yml`은 동일 Python과 잠금 목록을 선언한 추가 Conda 설정이며, Conda solver로 새 환경 생성까지 검증한 것은 아닙니다. MPS는 지원 장치에서만 선택하고 CPU 기준과 별도 실험으로 기록합니다. 다른 OS·BLAS·CPU의 근접 동점은 문서 ID 보조 정렬을 사용하더라도 달라질 수 있습니다. Q059의 기존 알려진 차이는 Top-5 집합 동일·4/5위 순서 교환입니다. 새로운 집합 변화는 자동 허용하지 않고 사유를 조사합니다.

## 공개 합성 검증

키·비공개 문서·모델 없이 계약 검증:

```bash
python scripts/aegisrag.py smoke
```

이 모드는 합성 detector adapter로 Gate/HMAC/Region 및 HCX 출력 계약을 검사하고 **모델·DB 미검증임을 출력**합니다. 고정 공개 테스트 키는 `app/smoke.py` 내부의 합성 fixture에만 사용됩니다. 실제 준비·채팅 로더로 넘어가지 않습니다. 공개 합성 테스트로 Holdout 검색 성능을 주장하지 않습니다.

모델·DB를 포함하려면 별도 일회용 PostgreSQL DB에 vector extension, `rag` schema, `rag_readonly` role을 준비하고, Git 밖의 권한 600 환경 파일로 연결을 지정합니다. 기존 스타터를 독립 Docker 프로젝트로 실행할 수도 있으나 스타터 포트 5433 충돌을 피하려면 기존 DB와 다른 호스트를 사용해야 합니다. 이 저장소의 Compose 파일을 수정하지 마세요.

```bash
python scripts/aegisrag.py init-db --env /safe/path/disposable.env
python scripts/aegisrag.py smoke --env /safe/path/disposable.env --write --device cpu
```

필수 환경 필드는 `POSTGRES_DB`, `DB_HOST`, `DB_PORT`, `DB_USER`, `RAG_PASSWORD`, 개인 `HF_TOKEN`입니다. 이 검증은 실제 A.X/Veil → Gate 처리와 고정 KoE5 → 새 PGVector 테이블 적재·검색, 반복 적재 멱등성, 잘못된 벡터 거부를 검사합니다. HCX는 mock 계약 검증입니다. 공개 키로 Holdout이나 공유 인덱스를 적재하지 마세요.

실제 합성 HCX 호출은 권한을 확인한 뒤 다음 명령으로 검사합니다. 외부에는 코드에 선언된 공개 합성 질문·문서만 전달합니다.

```bash
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/aegisrag.py smoke --hcx-live
```

 CI는 외부 API를 호출하지 않습니다. 인용 ID 검증은 문장별 사실적 근거 검증을 대체하지 않습니다.

## 비공개 데이터 공급과 전체 재적재

원본·Gold는 인터넷/GitHub가 아닌 팀의 승인된 별도 경로로 전달합니다. `local_eval/` 전체가 Git·Docker build context에서 제외됩니다. 기본 입력:

| 로컬 파일 | JSONL 필수 필드 | 수 |
|---|---|---:|
| `holdout_labeled.jsonl` | `id`, `text`; 기타 탐지 평가 라벨 보관 가능 | 187 |
| `holdout_retrieval_queries.jsonl` | `qid`, `query` | 88 |
| `holdout_retrieval_queries_gold.jsonl` | `qid`, `relevant_doc_ids`; 선택 `type`, `confusable_doc_ids`, `neutral_doc_ids` | 88 |

한 줄은 JSON 객체 하나입니다. ID는 영숫자·밑줄·하이픈 1~64자, 문서는 `text`, 질문은 `query` 문자열입니다. Gold는 준비 결과에 **평가용 별도 필드**로만 보관하며 탐지·보호·임베딩·테이블명 계산에 사용하지 않습니다. 준비 파일도 가명 텍스트 및 Gold를 포함하므로 비공개입니다. 파일 600/디렉터리 700으로 원자적 저장합니다. 같은 출력 파일을 덮어쓰지 않으므로 재실행 때 새 출력 경로를 사용하세요.

수신한 파일의 해시를 `config/holdout_manifest.json` 및 팀과 비교하세요. 다음 명령은 내용·절대 경로 없이 파일명·SHA256만 출력합니다. 공유할 때도 해시 manifest만 전달합니다.

```bash
python scripts/aegisrag.py manifest --files \
  step2_privacy_rag/local_eval/holdout_labeled.jsonl \
  step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl \
  step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl
```

준비→적재 대상 확인→`--confirm` 적재→`--baseline` 평가→테이블 선택 채팅은 루트 README 명령을 사용합니다. **기존 DB 데이터는 읽지 않고 같은 원본 전체를 새 테이블로 재처리**합니다. 다른 모델·키·정책·문서는 다른 실험이므로 기준 성능을 기대하지 않습니다. 기존 팀 HMAC 비밀키는 유지하고 비공개 채널로 공급받습니다. 동일 정규화 값·라벨·키만 같은 토큰을 만듭니다. 키 불일치 시 평가·채팅이 차단됩니다.

평가 가능 조건은 READY 질의이며 Gold 정답 중 최소 하나가 적재 문서에 있는 경우입니다. 기존 평가기와 동일하게 **채점 단계에서만 `neutral_doc_ids`를 제거**하고 Hit@1/5·Recall@5를 산출합니다. V6 기준 Recall@5 분모는 해당 질의 Gold relevant와 적재 문서의 교집합 크기입니다. 전체 Gold를 분모로 하는 `recall5_full_gold`도 함께 출력합니다(기존 데이터에서는 0.8791). MRR은 중립 제거 후 전체 순위의 첫 정답 역순위입니다. 온라인 Top-5 결과는 중립 정보로 수정하지 않습니다. 지표는 81질의 macro 평균, 소수 네 자리입니다.

기존 V6 DB와 비교만 할 때:

```bash
python scripts/aegisrag.py evaluate-v6 \
  --input step2_privacy_rag/local_eval/reproduction/prepared.json \
  --table rag.aegis_koe5_v6_exp1 --baseline
```

## 교체 인터페이스와 계측

자체 탐지기를 붙일 때 `detect(text)`가 `status`, 라벨·문자 오프셋 `[start,end)`의 `entities`, 보호 구간 `mask_intervals`를 반환해야 합니다. Gate는 추가로 `ax_predict(text)`, `veil_predict(text)` 후보를 사용하므로 이 계약도 adapter로 제공해야 합니다. `mask_intervals`가 Strict entities보다 넓을 수 있으며 축소하면 안 됩니다. 기존 READY/REVIEW/BLOCKED 계약을 유지하세요. `create_detector()` 팩터리를 교체하는 adapter는 별도 variant 코드/실험 ID로 관리합니다. 다른 모델로 baseline 성능이 유지된다고 가정하지 않습니다.

DB 연결은 `app/settings.py`, SQL 적재는 `app/reproduction.py`, 읽기는 `app/core.py`에 분리했습니다. 운영 DB로 이식 시 DSN에 대응하는 환경 필드만 교체하고 기존 테이블 파괴 없이 새 적재 대상으로 검증합니다. 현재 DB 접근 계정 설정의 변경은 수행하지 않습니다.

개발자 상세의 `step1_ms`는 탐지기 로딩·Gate·가명처리 포함, `embedding_ms`는 최초 KoE5 로딩 포함, `db_ms`는 연결·검증·전체 후보 조회, `rerank_ms`, `hcx_ms`, `total_ms`를 구분합니다. 원문 질문·키·문서를 서버 로그에 남기지 않습니다. 첫 요청 모델 로딩과 매 질문 Step1 재로딩이 비용에 포함되며 이번 작업에서는 캐싱·정책 튜닝을 하지 않습니다.

## 오류 FAQ

- HF 접근 실패: 두 private repo의 개인 계정 권한, 개인 `HF_TOKEN`, 정확한 revision 다운로드 가능 여부를 확인합니다. 접근 불가를 성공/skip 처리하지 않습니다.
- DB 연결 실패: Docker health와 **기존 `.env`의 DB_PORT**를 확인합니다. 스타터는 호스트 5433을 사용합니다. `init-db`가 실패하면 적재하지 않습니다.
- 기존 볼륨에 역할/스키마 없음: 초기화 스크립트를 재실행하거나 테이블을 비우지 말고 관리자에게 확인하거나 별도 새 DB를 준비합니다.
- HMAC mismatch: 팀 기존 키와 준비 파일의 키 tag가 일치해야 합니다. 토큰·키를 로그로 공유하지 마세요. 다른 키라면 별도 전체 재처리가 필요합니다.
- 목표 테이블 확인 실패/기존 내용 불일치: 출력된 정확한 해시 테이블명을 사용합니다. 코드·모델·원본 변경은 새 대상이 됩니다. 기존 테이블을 지워 해결하지 않습니다.
- Holdout 파일 없음: 승인된 별도 경로로 제공받기 전에는 해당 성능을 재현할 수 없습니다. 공개 합성 테스트만 실행할 수 있습니다.
- 출력 파일 있음: 준비 단계는 덮어쓰기를 거부합니다. 새로운 비공개 출력 경로를 지정하세요.
- Step1 reference parity skip: 과거 release notebook이 저장소에 없어 비교 1건은 실행할 수 없습니다. 실제 현재 모델 준비/추론·나머지 회귀와 구분해서 보고합니다.
- HCX 전송 미승인: 서비스는 시작을 거부합니다. 데이터 권한 확인 후에만 `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES`를 설정합니다. API 오류·지원하지 않는 인용은 차단합니다.

Git 검사 `python scripts/scan_git.py --history`는 인덱스 및 일반 push 대상인 HEAD 이력에서 민감 경로·현재 `.env`의 비밀값·일부 토큰 패턴을 검사합니다. 알려지지 않은 모든 비밀값의 부재를 수학적으로 보증하지는 않습니다. 이번 정리에서는 비공개 파일을 추적하지 않으며, 과거 Git 이력을 재작성하지 않습니다.

로컬 Codex 자동 캡처 ref(`refs/codex/turn-diffs/...`)에는 작업 시작 당시 미추적 비공개 평가 파일이 포함되어 있었습니다. `main` 및 원격 main 이력과 분리되어 있으며 일반 `git push origin main`은 이 ref를 보내지 않습니다. `git push --mirror`/`--all`이나 이 ref의 명시적 push를 사용하지 마세요. `scan_git.py --all-refs`는 이 로컬 캡처까지 검사하므로 비공개 경로를 보고합니다. 사용자 작업/도구 복구 이력을 보존하기 위해 로컬 캡처 ref는 변경하지 않았습니다.

파일명은 `privacy_gate.py`, `document_preparation.py`, `dataset_preparation.py`, `region_keys.py`처럼 역할명으로 관리합니다. 이전 파일명과의 대응은 실험 이력에 보존합니다. 파일명도 코드 provenance 해시에 포함되므로 이번 이름 정리 이후 새 적재 테이블 해시는 달라지며, 기존 테이블은 `--table`/`AEGISRAG_TABLE`로 계속 읽을 수 있습니다.
