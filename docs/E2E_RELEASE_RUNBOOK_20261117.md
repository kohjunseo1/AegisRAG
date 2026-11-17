# AegisRAG E2E 임시 완성 실행서 (2026-11-17)

## 고정 조건
- 사용 DB `rag.aegis_live_docs` 187행. 읽기 전용. 기존 HMAC·Gate·KoE5 순위와 Evidence-K=5 유지.
- CrossEncoder/Title/Intent Fusion은 미채택. 신규 재랭커 호출 없이 HCX를 최대 한 번 호출한다.
- 기존 88 Holdout은 반복 검토된 개발용 데이터이며 신규 Blind 점수로 표시하지 않는다.
- Gate의 REVIEW/BLOCKED는 강제 READY 변환하지 않는다.
- HCX API 호출은 사용자가 가명처리 문서의 외부 전송을 승인한 범위에서만 실행한다.

## A. Gate 3건 로컬 원인 분석 (외부 전송 없음)

```bash
cd ~/AegisRAG-holdout-e2e
git pull --ff-only origin main
python scripts/diagnose_gate_queries.py --qids Q001 Q015 Q033
```

- Q001/Q015: `unresolved_veil_person`이므로 Veil 정책의 `ambiguous` 원인·숫자형 후보와 분리 조사. 모델 오류를 이유로 Gate를 생략하지 않는다.
- Q033: `invalid_number_value`이므로 `numeric_normalization_failures`의 라벨·길이·허용 문자 여부를 확인한다. 전체 숫자값/원문/정규화값은 로그에 찍지 않는다. 동일한 원문 번호가 DB에 가명토큰으로 있다면 로컬 정확한 식별자 매칭이 목표다.
- 해당 오류가 해결될 때까지 Gate의 안전한 CLARIFY/BLOCKED 응답을 유지한다.

## B. 16건 유형 균형 E2E 파일 생성 (외부 전송 없음)

```bash
export AEGISRAG_TABLE=rag.aegis_live_docs
export AEGISRAG_INDEX_MODE=live
export AEGISRAG_LIVE_MIN_ROWS=187
export AEGISRAG_TOPIC_BOOST=0

python scripts/build_e2e_suite.py --size 16 \
  --out step2_privacy_rag/local_eval/e2e_pilot_16.jsonl
python scripts/eval_chat_mvp.py --device cpu \
  --cases step2_privacy_rag/local_eval/e2e_pilot_16.jsonl
```

- 16건은 Gold의 `type` 범주를 균형 있게 순환 선택하며 Gold 문서 ID를 랭킹/질문 생성에 사용하지 않는다.
- 16건 파일이 이미 있으면 자동 덮어쓰지 않는다. 다른 파일명을 지정한다.
- 로컬 점검이 먼저 통과해야 HCX를 호출한다.

## C. 승인된 범위에서 실제 HCX E2E

```bash
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/eval_chat_mvp.py --device cpu --hcx-live \
  --cases step2_privacy_rag/local_eval/e2e_pilot_16.jsonl \
  --report step2_privacy_rag/local_eval/e2e_pilot_16_hcx_review.json
```

- HCX 비용 및 가명처리된 검색 근거의 외부 전송이 발생한다.
- 답변 원문은 `local_eval`의 소유자 전용 JSON에만 남기고 stdout에는 쓰지 않는다.
- `ANSWER`, `CLARIFY`, `INSUFFICIENT_EVIDENCE`, `BLOCKED_OR_DEFERRED` 및 Gate 상태를 별도로 기록한다.
- **검색 결과에 Gold 문서 ID가 있는 것과 정답 사실성이 맞는 것은 별개**다.

## D. 사람의 사실 검증과 최종 점수

각 `cases[]`의 Human 필드를 `true/false`로 채운다. 모르는 값은 `null`로 두고 미채점 처리한다.

- 공통 필수: `human_decision_appropriate`, `human_privacy_safe`
- `ANSWER`에 추가 필수: `human_answer_correct`, `human_citation_supported`, `human_answer_complete`, `human_temporal_correct`
- 시점이 없는 답변에는 `human_temporal_correct=true`를 명시적으로 표시한다 (해당 오류 없음).
- 검색 Gold의 목록이 불완전할 수 있으므로 인용 문서가 Gold 밖이라는 이유만으로 자동 false 판정하지 않는다. 인용 원문의 주장 지지 여부를 검수한다.
- `CLARIFY`/보류의 성공 여부는 **실제 모호성·보호 정책에 적합했는지** 보고 결정한다. 검색 실패 때문에 불필요하게 거절하면 false로 표시한다.

```bash
python scripts/score_e2e_human_review.py \
  --report step2_privacy_rag/local_eval/e2e_pilot_16_hcx_review.json
```

`pending`이 0이 되어야 평가가 완결된다. `success_rate_scored`는 완료된 건만의 성공률이고 `success_rate_all`은 미채점까지 분모에 포함한 비율로 구분한다.

## E. 88건 평가로 확대

외부 전송 승인·사실 검증 리소스가 준비된 경우, 전체 88건에 대해 다른 파일명으로 `--size 88` 스위트를 만들고 동일하게 실행한다.

```bash
python scripts/build_e2e_suite.py --size 88 \
  --out step2_privacy_rag/local_eval/e2e_full_88.jsonl
```

## 완료 조건
1. 정상 검색 187행 DB / 88질의 기존 검색 결과 회귀 없음.
2. Q001/Q015 REVIEW, Q033 BLOCKED의 원인을 기록하고 오인·미보호 자동 통과 없음.
3. HTTP 응답 + HCX 호출/예외 처리의 단위·통합 테스트 통과.
4. 실제 생성 답변을 문서 사실·완전성·시점·인용·개인정보 안전성으로 채점.
5. Holdout과 독립된 신규 Blind 질문으로 일반화 검사. **90% 이상은 목표이지 현재 실측치가 아님.**
