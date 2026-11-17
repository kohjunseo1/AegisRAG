# Evidence-Aware HCX A/B 비교 실험 — 실전2

## 목적
기존 READY 검색 Hit@5 83/85는 **최종 답변 정확도가 아니다**. 전체 88 중 실제 HCX Pilot 16건이 실행되었고, 그중 3건만 문서 본문까지 깊게 검수했다. 나머지는 정확하다고 단정할 수 없다. 55건을 확인했다고 보고하지 않는다.

이번 실험은 **Top-5 문서의 ID·본문·순서 불변**을 유지하면서, HCX에 기존 HMAC 일치 개수와 해당 라벨 유형을 "정보"로 제공했을 때 Grounding이 좋아지는지 비교한다. HCX는 각 질의에서 최대 1회만 호출한다. 메타데이터와 별도 가이드의 효과를 구분할 수 있게 3개 모드를 제공한다.

- \`baseline\`: 기존 HCX 프롬프트와 기존 문서 필드만 전송. 기본 모드. 변경 없음.
- \`metadata\`: 같은 Top-5에 \`query_hmac_matches\`와 \`matched_entity_types\`만 추가. 전부 로컬에서 계산. 검증되지 않은 신원 관계를 확정하지 않는다.
- \`metadata_guidance\`: \`metadata\` + 일반적인 식별자/근거 선택 지침 추가.

추가 LLM/임베딩/DB 쓰기 없음. 추가 입력 토큰·지연은 실측해 판단한다. **QID·Gold ID별 자동 보정/필터 없음.**

## 브랜치 격리 및 기존 파일 보호

기존 \`~/AegisRAG\`와 \`~/AegisRAG-e2e-check\` 작업을 건드리지 않는 별도 worktree에서 실행한다.

\`\`\`bash
cd ~/AegisRAG
git fetch origin experiment/evidence-aware-hcx-20261117
git worktree add --detach ../AegisRAG-hcx-ab \
  origin/experiment/evidence-aware-hcx-20261117
cd ~/AegisRAG-hcx-ab
ln -s ../AegisRAG/.env .env
\`\`\`

위 폴더가 이미 존재하면 worktree add 또는 링크를 반복하지 않는다. 기존 임시 데이터 및 미커밋 파일 보존.

## 오프라인 합성·회귀 테스트

\`\`\`bash
cd ~/AegisRAG-hcx-ab
export PYTHONPATH="$PWD:$PWD/step2_privacy_rag/app:$PWD/step1_pii_detector/src"
python -m unittest discover -s tests -p 'test_hcx_evidence_ab.py' -v
python -m unittest discover -s tests -p 'test_compare_hcx_variants.py' -v
\`\`\`

## 실모델 + DB E2E (HCX 미호출)

\`\`\`bash
python scripts/verify_release_http.py \
  --queries ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl
\`\`\`

## HCX 유형 균형 16건 + 식별자 오류 진단 1건 공정 비교 — 외부 전송 승인된 데이터에만

**같은 브랜치·같은 모델·같은 17개 질문·같은 문서·같은 순위**에서 테스트해야 한다. 이전 구버전 Pilot을 이번 실험의 공정 Baseline으로 혼용하지 않는다. 다음 2회는 약 30번의 HCX 호출 비용이 발생할 수 있다. Gate 실패 2건은 호출되지 않는다.

\`\`\`bash
cd ~/AegisRAG-hcx-ab
export AEGISRAG_TABLE=rag.aegis_live_docs
export AEGISRAG_INDEX_MODE=live
export AEGISRAG_LIVE_MIN_ROWS=187
export AEGISRAG_TOPIC_BOOST=0
export AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES

python scripts/build_e2e_suite.py --size 16 --include-qids Q027 \
  --queries ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries.jsonl \
  --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
  --out step2_privacy_rag/local_eval/e2e_pilot_17.jsonl
CASE_FILE=step2_privacy_rag/local_eval/e2e_pilot_17.jsonl

AEGISRAG_HCX_EVIDENCE_MODE=baseline \
python scripts/eval_chat_mvp.py --hcx-live --device cpu \
  --cases "$CASE_FILE" \
  --report step2_privacy_rag/local_eval/ab_baseline_review.json

AEGISRAG_HCX_EVIDENCE_MODE=metadata_guidance \
python scripts/eval_chat_mvp.py --hcx-live --device cpu \
  --cases "$CASE_FILE" \
  --report step2_privacy_rag/local_eval/ab_metadata_guidance_review.json

python scripts/compare_hcx_variants.py \
  --baseline step2_privacy_rag/local_eval/ab_baseline_review.json \
  --variant step2_privacy_rag/local_eval/ab_metadata_guidance_review.json
\`\`\`

기존 파일이 존재하면 report 경로를 변경한다. 스크립트는 덮어쓰지 않는다.

## 평가 원칙
- \`HCX_AB_COMPARE\`는 decision/citation 변화·보존된 Top-5·토큰/latency 비용을 보고한다. **답변 사실성을 자동 판정하지 않는다.**
- Q027·Q048·Q075 외의 기존 성공 사례에서도 회귀가 없는지 반드시 검수한다.
- 개선 채택: 잘못된 대상 답변 감소, 정당한 답변 유지, 불필요한 거절 증가하지 않음, 허용 가능한 추가 토큰/지연. 골드 불완전성은 수동으로 보정해 판단.
- \`metadata_guidance\`가 개선되면 \`metadata\` 단독을 동일 조건에서 추가 측정해 메타데이터 효과와 프롬프트 효과를 구분한다.
- 17개가 개선되더라도 일반화는 검증되지 않음. 88개 기존 Holdout은 개발용이고 신규 Blind 질문이 별도 필요하다.
- 정보가 부족하거나 신규 모델/DB 접근이 없으면 실측을 지어내지 않는다.
