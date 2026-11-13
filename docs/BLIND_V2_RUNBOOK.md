# Blind Clean v2 — 실제 실행 가이드 (171문서 + 28 Zero-PII + 100질의)

> 기준: 기존 187문서 운영 DB `127.0.0.1:5433` 보호, 5544에 격리된 신규 데이터 실험. GitHub에는 원본·질문·Gold·개인정보·비밀키를 올리지 않습니다. 모든 명령은 AegisRAG 루트에서 실행합니다. 합성 CI 통과는 로컬 171문서 모델·DB 실험 완료를 뜻하지 않습니다.

## 데이터와 평가 목적

| 파일 | 데이터 | 목적 |
|---|---|---|
| `blind_eval.jsonl.zip` | **171 문서** (16 유형, PII Gold 1,439 span, 그중 Zero-PII 19문서) | Step1 Strict F1/Mask Recall + Gate + 문서 스테이징·검색 인덱스 |
| `pii_negative_controls_28.jsonl` | **28 Zero-PII 문서** (기존 171에 부족한 14개 유형 × 2) | 문서 유형에 따른 False Positive/불필요한 REVIEW 편향 점검; **171문서 검색 인덱스에 섞지 않음** |
| `blind_queries_100.jsonl` | **100 검색 질문** (dev40 / validation30 / challenge30) | 유형별 검색·답변 QA; 개인정보 표현이 포함될 수 있으므로 비공개 |
| `blind_gold_100.jsonl` | 질문별 원본 `Bxxxx` 문서 ID, 기대 답변, 근거 구절, ANSWER/CLARIFY/INSUFFICIENT_EVIDENCE | 검색 Hit/Recall + 인용 지지 + 답변 수동 채점 |
| `QA_AUDIT.md` / `human_review_template_100.jsonl` | 정제 이력과 채점 양식 | 날짜·요일 정정, 평가 결과 기록 |

**날짜 빈 필드의 의미:** 원본 B0003/B0012/B0066/B0153/B0156/B0275에 `[오늘 날짜]`, B0008에 `*년 *월 *일`이 실제로 있습니다. 이는 문서의 미기입 값으로 **추측 보완하지 않았습니다**. 날짜·요일이 서로 모순되는 7곳만 정정해 PII Gold 오프셋은 보존했습니다.

## 0. 브랜치와 파일 준비

~~~bash
git switch fix/v6-chat-index-hcx-hf-org-20261112
git pull --ff-only
git branch --show-current
umask 077
mkdir -p step2_privacy_rag/local_eval/blind_v2
unzip -oq ~/Downloads/AegisRAG_Blind100_Benchmark_v2.zip \
  -d step2_privacy_rag/local_eval/blind_v2
~~~

해당 ZIP은 ChatGPT에서 받은 평가 패키지입니다. 실제 다운로드 위치가 다르면 파일 경로만 바꿉니다. 압축 해제된 9개 파일은 모두 `local_eval/` 아래에 두세요.

## 1. 질문–Gold 무결성 검증 (빠르고 안전, 모델/DB 없음)

~~~bash
python scripts/prepare_blind_100.py \
  --documents step2_privacy_rag/local_eval/blind_v2/blind_eval.jsonl.zip \
  --queries step2_privacy_rag/local_eval/blind_v2/blind_queries_100.jsonl \
  --gold step2_privacy_rag/local_eval/blind_v2/blind_gold_100.jsonl \
  --check-only
~~~

기대 출력: `BLIND_QA_GOLD_CHECK` + 171 문서 / 100 문항 / ANSWER 80 / CLARIFY 12 / INSUFFICIENT_EVIDENCE 8. 데이터 ID와 근거 구절에 대한 기계적 무결성을 확인할 뿐, 100문항에 대한 2인 독립 채점이 완료됐다는 뜻은 아닙니다.

## 2. 실전1: 최초 Blind 모델 성능 — 반드시 먼저 수행

~~~bash
TOKENIZERS_PARALLELISM=false \
python scripts/eval_blind_pii.py \
  --input step2_privacy_rag/local_eval/blind_v2/blind_eval.jsonl.zip \
  --report step2_privacy_rag/local_eval/blind_v2/pii_first_report.json
~~~

정상 완료 후 `BLIND_PII_METRICS`: Strict F1, per-type F1, Step1 mask Gold 문자 recall, projected READY/REVIEW/BLOCKED, 자동 READY 중 의심 미보호 Gold 문자 등. 어떤 모델/룰도 수정하지 않은 **첫 점수**를 보존합니다.

~~~bash
TOKENIZERS_PARALLELISM=false \
python scripts/eval_blind_pii.py \
  --input step2_privacy_rag/local_eval/blind_v2/pii_negative_controls_28.jsonl \
  --report step2_privacy_rag/local_eval/blind_v2/negative_first_report.json
~~~

Zero-PII 28은 원래 171 데이터셋의 Strict F1 분모에 합산하지 않고, 원래 Zero-PII 19와 합해 유형 커버리지의 **보조 오탐 진단**에 사용합니다.

> `--report` 출력은 기존 파일을 덮어쓰지 않습니다. 같은 테스트를 다시 실행할 때는 새로운 보고서 이름을 쓰세요. **PII 결과에서 심각한 자동 READY 누출 의심이 있으면 여기서 적재를 중단하고 직접 조사하세요.**

## 3. 5544 격리 DB와 비공개 환경 구성

~~~bash
docker compose --env-file .env \
  -f step2_privacy_rag/compose.blind.yaml up -d

cp .env step2_privacy_rag/local_eval/blind_v2/blind.env
cat >> step2_privacy_rag/local_eval/blind_v2/blind.env <<'EOF'
DB_HOST=127.0.0.1
DB_PORT=5544
POSTGRES_DB=aegis_blind_test
AEGISRAG_TABLE=rag.aegis_live_docs
AEGISRAG_INDEX_MODE=live
AEGISRAG_LIVE_MIN_ROWS=1
EOF
chmod 600 step2_privacy_rag/local_eval/blind_v2/blind.env
~~~

`DB_USER=rag_ingest`와 기존 HMAC 키를 재사용합니다. 테스트용 DB 인스턴스는 기존 5433과 별도입니다. `DB_HOST/DB_PORT/POSTGRES_DB`가 셸 환경변수에 미리 설정되어 있으면 .env보다 우선하므로 정확한 값으로 교정해야 합니다. **5544가 연결되지 않거나 기존 운영 DB와 분리되지 않았다면 계속 진행하지 마세요.**

## 4. 문서 준비(Stage) → REVIEW

~~~bash
unzip -p step2_privacy_rag/local_eval/blind_v2/blind_eval.jsonl.zip \
  blind_eval.jsonl > step2_privacy_rag/local_eval/blind_v2/source.jsonl

python scripts/dataset_intake.py stage \
  --input step2_privacy_rag/local_eval/blind_v2/source.jsonl \
  --dataset blind_v2 --id-field id --text-field text \
  --type-field doc_type \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --stage step2_privacy_rag/local_eval/blind_v2/stage.json

python scripts/dataset_intake.py status \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --stage step2_privacy_rag/local_eval/blind_v2/stage.json
~~~

처리 단계에서는 Bxxxx Gold를 입력하지 않습니다. 원본 `doc_type`은 가명처리되는 문서 텍스트에 별도 표제로 포함되므로, 원본 PII start/end 오프셋을 Stage 결과에 직접 적용해서는 안 됩니다.

~~~bash
python scripts/dataset_review_ui.py \
  --stage step2_privacy_rag/local_eval/blind_v2/stage.json \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --port 8781
~~~

브라우저에서 http://127.0.0.1:8781/ 접속 → 터미널의 `REVIEW_ACCESS_TOKEN`으로 로그인 → REVIEW 항목 원문 검토 → 필요한 부분 수정 → **동일 Step1/Gate 재검증** → 승인. 현재 테스트 관리자 UI는 다른 DB로 실수로 쓰지 못하도록 `aegis_blind_test / 5544 / localhost` 외 DB UPSERT를 거부합니다.

## 5. 첫 번째 승인 배치 → 빈 테스트 DB 초기 적재

~~~bash
python scripts/dataset_intake.py export \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --stage step2_privacy_rag/local_eval/blind_v2/stage.json \
  --output step2_privacy_rag/local_eval/blind_v2/approved.json

python scripts/bootstrap_blind_db.py \
  --input step2_privacy_rag/local_eval/blind_v2/approved.json \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --device cpu \
  --confirm BOOTSTRAP_BLIND_TEST_DB
~~~

처음 한 번만 bootstrap. 이미 초기 적재한 뒤 더 많은 REVIEW를 승인했다면 새 배치를 만든 후 8781 UI의 **DB 반영** 버튼으로 기존 행을 변경하거나 신규 승인 문서를 증분 UPSERT합니다. 테스트 DB만 사용하세요.

## 6. 질문 100개를 불투명 DB ID로 변환

~~~bash
python scripts/prepare_blind_100.py \
  --documents step2_privacy_rag/local_eval/blind_v2/blind_eval.jsonl.zip \
  --queries step2_privacy_rag/local_eval/blind_v2/blind_queries_100.jsonl \
  --gold step2_privacy_rag/local_eval/blind_v2/blind_gold_100.jsonl \
  --namespace blind_v2 \
  --env step2_privacy_rag/local_eval/blind_v2/blind.env \
  --split dev \
  --out step2_privacy_rag/local_eval/blind_v2/eval_dev.jsonl
~~~

원본 Gold `Bxxxx`와 DB에 저장되는 `blind_v2__<HMAC>`는 직접 비교할 수 없습니다. 변환 스크립트가 동일 key와 namespace로 매핑합니다.

## 7. 실전2: 검색 평가 (HCX 없음)

~~~bash
DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live \
AEGISRAG_LIVE_MIN_ROWS=1 AEGISRAG_TOPIC_BOOST=0 \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/blind_v2/eval_dev.jsonl --limit 10
~~~

첫 10개가 문제없이 통과하면 `--limit 10`을 제거해 dev 40문항 평가. 지표: Gate 상태·indexable Gold coverage·Hit@1/5·MRR@5·Recall@5·CPU 지연시간. **CLARIFY 12개/근거 부족 8개는 관련 Gold가 빈 목록인 것이 정상**이며, 검색 Hit 분모에서 제외해야 합니다.

## 8. 실제 HCX 답변 + 인용 평가 (유료 API·명시적 승인 필요)

~~~bash
DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live \
AEGISRAG_LIVE_MIN_ROWS=1 AEGISRAG_TOPIC_BOOST=0 \
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/blind_v2/eval_dev.jsonl \
  --hcx-live --limit 10 \
  --report step2_privacy_rag/local_eval/blind_v2/hcx_first_10.json
~~~

로컬 보고서에는 질문·기대 답변·HCX 실제 답변·인용 ID·사람 채점용 빈 필드가 기록됩니다. 확인 지표: 답변 사실성, 실제 문서 인용 지지 여부, 과잉 인용, 환각/원문 PII 누출, 불필요한 거절. 실제 HCX 호출은 비용을 발생시킵니다.

## 9. 테스트 웹 챗봇 (기존 8768 대신 8769)

~~~bash
DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live \
AEGISRAG_LIVE_MIN_ROWS=1 AEGISRAG_TOPIC_BOOST=0 \
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/aegisrag.py run-chat --device cpu --port 8769
~~~

브라우저: http://127.0.0.1:8769/ . 기존 운영 챗봇 8768 / DB 5433과 분리합니다.

## 10. 실험 완료 기준

1. **실전1 외부 Gold:** PII Strict F1, Mask Recall, 유형별 오류; 자동 READY에서 미보호 Gold 점검.
2. **Zero-PII:** 유형별 False Positive/불필요한 REVIEW, 정상 문장 손실.
3. **문서 운영 E2E:** READY/REVIEW/BLOCKED, 수동 수정/재검증, 격리 DB 첫 적재/추가 반영/중복 재실행 안정성.
4. **검색:** 승인되어 실제 인덱스에 있는 Gold 기준 Hit@K/Recall@K; 미적재 Gold는 별도 보고.
5. **HCX:** 답변 사실성과 인용 지지·과잉 인용·개인정보 안전성·보류 적절성.
6. **기존 회귀:** 187문서/88질의 동결 기준선 유지 여부 점검.
7. **최종 블라인드:** dev40 → validation30 → challenge30 순서. 문서 자체는 공유하므로 완전한 독립 외부 코퍼스는 아님.

### 데이터와 평가의 한계

- 문서 ID/PII 오프셋/Gold 근거 문자열의 자동 일치 검사는 통과했지만, 모든 모범답변에 대한 독립 2인 검증은 하지 않았습니다.
- 일부 문서의 `[오늘 날짜]` / `*년 *월 *일`은 원문에 실제로 있는 미입력 필드입니다. 답변 생성 시 현재 날짜를 덮어씌우지 않습니다.
- 문서 탐색 질문은 관련 문서가 복수이므로 단순 Hit@5만으로 답변 충분성을 판단하지 않습니다.
- 이름 표현 마스킹은 평가하지만 동명이인 자동 연결은 이번 MVP 요구사항 밖입니다.
