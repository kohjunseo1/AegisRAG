# AegisRAG 실전1·2 임시 최종 출고 체크리스트 (2026-11-17)

## 목적과 동결 대상
기존 187행 live DB와 기존 88질의 평가를 보존한다. 실전1(A.X/Veil→V5→Gate→HMAC)과 실전2(질문 Gate→검색→HCX→답변/인용)를 실제로 연결해 검증한다.

동결: 187행 rag.aegis_live_docs, HMAC 키, 모델 버전, Gold, 재현된 Hit@1 77/85 및 Hit@5 83/85. 검색 QID 특화 튜닝·추가 규칙·DB 재생성 금지.

## 왜 177+10과 150+37이 달라졌는가
과거 dataset_preparation.py의 인덱싱 후보 조건은 state == LOCAL_PREPARED. 신규 dataset_workflow.py의 staging은 추가로 unlinked_mentions == 0과 잔여 PII 검사를 요구한다. 새 결과 150 READY / 37 REVIEW는 27 UNLINKED, 9 STEP1, 1 GATE. 모델 저하나 기존 187행 DB 삭제를 뜻하지 않는다. 개인별 문서 1:1 비교 결과는 아직 확보되지 않았다.
신규 staging의 37 REVIEW는 후속 작업으로 분리한다. 임시 최종 챗봇 검증을 막는 새로운 선행 과제로 취급하지 않는다.

## 현재 근거
로컬 실측 Holdout 187 docs / 88 queries / 88 Gold, key_matches=true, DB READY 187.
질의 Gate READY 85 REVIEW 2 BLOCKED 1. READY Hit@1 77/85, Hit@5 83/85, MRR 0.9371, Recall@5 0.8848 (--eval-raw 정의).
eval_chat_mvp의 MRR은 전체 88/Top-5 제한 등으로 분모가 다르므로 위 MRR과 직접 비교 금지.
실제 HCX 답변 정확도/claim-level grounding 및 승인된 Holdout 유출 안전성은 아직 측정하지 않음.

## 우선 실행: HTTP까지의 실모델·DB E2E (HCX 0회)
cd ~/AegisRAG-holdout-e2e
git pull --ff-only
python scripts/verify_release_http.py

위 스크립트는 187행 인덱스를 읽고 실제 A.X/Veil, Gate, HMAC, 검색과 localhost HTTP health/index/chat를 확인한다. 88개 질문 중 READY와 DEFERRED를 소수 선택한다. HCX 호출은 코드로 가로막아 발생하면 실패한다. 원문·비밀키를 로그로 남기지 않는다. DB 불변 검증. 정답 사실성 평가는 아님.

## 직접 웹 확인 (외부 전송 없음)
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live AEGISRAG_LIVE_MIN_ROWS=187 AEGISRAG_TOPIC_BOOST=0 python scripts/aegisrag.py run-chat --device cpu --port 8768 --local-only
localhost 8768 웹 화면에 검색 결과만 표시하고 LOCAL_SEARCH_ONLY/CLARIFY/BLOCKED/INSUFFICIENT_EVIDENCE를 구분한다. HCX가 없으면 답변 생성이라고 주장하지 않는다.

## 실제 HCX 답변 E2E
합성 HCX 호출은 명시적 승인·비용 한도에서 다시 확인한다. 실제 비공개 Holdout 문서는 잔여 PII 위험 평가 및 외부 전송 승인 전에는 전송하지 않는다. 승인 후 실행한 eval_chat_mvp --hcx-live의 결과에 대해 답변 정확도·claim support·citation precision·누출을 로컬 human report로 평가한다. 인용 ID가 유효하다는 것만으로 주장 사실성이 증명되지 않는다.

## 브랜치 단일 통합 방향
현재 main ← PR#7 (fix/v6...) ← PR#8 (feat/query-safety...) ← PR#9 (feat/holdout-e2e...). PR#6은 별도 draft 실험이다.
새 브랜치는 만들지 않고 기존 worktree에서 로컬 미커밋 변경을 먼저 보호·검토한다. 기존 로컬 8개 REVIEW 파일이 이미 복사됐을 수 있으니 재복사·강제 덮어쓰기 금지.
실모델/DB/HTTP/합성 API 안전성 검증을 완료한 후 기존 PR#9의 base를 main으로 바꿔 전체 #7~#9 변경을 단일 PR diff로 검토한다. 통합 확인 전 다른 PR 닫기, 브랜치 삭제, reset --hard, clean -fd, 강제 push 금지.

## 출고 판정
1) 개인정보 보호 회귀와 187/88 검색 재현, 2) HTTP READY/REVIEW/BLOCKED/근거 없음 검증, 3) 승인된 HCX 경로의 통신·응답·인용/개인정보 평가, 4) CI와 실제 Mac 실행 결과 구분, 5) 원본 DB/비밀 파일 보존 및 단일 main 통합.
현재: 기존 검색 PASS / 로컬 HTTP 실모델 추가 검증 필요 / HCX 실측 답변 정확도 미완료.
