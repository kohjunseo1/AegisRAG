# AegisRAG 질의 단계 공통 오류 처리 정책 (2026-11-17)

## 목표
기존 Step1 → Privacy Gate → HMAC → Retriever → HCX의 fail-closed 계약을 보존한다.
질의는 문서 ingestion과 달라 개발자가 매번 REVIEW 큐를 처리하지 않는다.
사용자에게 개인정보 없이 다시 질문할 수 있는 구체적인 방향을 제공한다.
특정 QID, 이름, 데이터셋 용어, 테스트 정답 문서 ID는 분기 조건에 쓰지 않는다.
정상 경로에 새 LLM·임베딩·추론 모델을 추가하지 않는다.

## 의사결정 표

| 원인 | 내부 처리 | 사용자 메시지 원칙 | HCX 호출 |
|---|---|---|---|
| READY + 충분한 근거 | 기존 검색 Top-5 → HCX | 정확한 날짜, 인용, 관련 문서 | 최대 1회 |
| READY + 근거 있으나 불충분 | HCX INSUFFICIENT_EVIDENCE | 추측하지 않고 업무/문서 종류 제시 | 최대 1회 |
| READY + 질문 자체 모호 | HCX CLARIFY | 확인 질문 1개, 개인정보 재요구 금지 | 최대 1회 |
| REVIEW, 미확정 인물 경계 | CLARIFY | 이름/식별자 없이 문서 종류·업무 주제 | 0회 |
| REVIEW, 미보호 주소 후보 | CLARIFY | 상세주소 대신 지역/업무 주제 | 0회 |
| 다른 REVIEW | CLARIFY | 개인정보 대신 문서 종류/업무 주제 | 0회 |
| BLOCKED, 정규화 실패 | BLOCKED_OR_DEFERRED | 해당 식별자의 가명처리 실패 안내, 원문 반복 입력 요구 금지 | 0회 |
| 다른 BLOCKED | BLOCKED_OR_DEFERRED | 안전하게 처리 불가 안내 | 0회 |
| HCX 타임아웃/한도/5XX/응답 무효 | TEMPORARY_ERROR | 일시적 생성 장애, 근거 없음이나 개인정보 차단과 혼동 금지 | 1회 시도 |
| DB 오류/Step1 모델 실패 | 안전한 HTTP 오류 | 비밀정보를 포함하지 않는 코드, 운영 진단 | 0회 |

## 현재 구현 범위
- Gate 실패 분류: `document_preparation.Outcome.safe_issue_code` (allowlist)
- 라이브 파이프라인 내부 전달: `LivePipeline.last_query_issue_code` (요청별 초기화)
- 답변 분기: `query_privacy_controller.unresolved_query_response`
- HCX 장애 분기: `app.respond`의 제한된 OutboundError allowlist
- 브라우저 상태: `app.js`의 한국어 결정 레이블
- 과거 시점·중복 문서·핵심 주장·개인정보 토큰 표현: `hcx.SYSTEM`
- 모든 fail-closed 경로는 기존 Privacy Gate/BLOCKED를 READY로 변경하지 않음.

## 반드시 나중에 해결할 기술 부채
1. numeric span misclassification: 숫자형 PII의 부분 스팬(예: 실제 길이보다 짧은 card_number) 발견 시 임의의 다른 라벨로 HMAC하면 안 된다. 전체 스팬과 형식/유형을 독립적으로 검증할 수 있을 때만 자동 복구하되, 동일 키·타입·정규화 계약을 반드시 유지한다. 임시 상태는 BLOCKED_OR_DEFERRED.
2. Query Gate REVIEW는 관리자 문서 큐에 넣지 않는다. 실제로 안전하게 검증된 독립적 후보 복구만 허용. 검증 불가능하면 사용자에게 개인정보를 빼고 업무 주제로 다시 질문하도록 한다.
3. 접근 제어: 번호만으로 고객 정보를 검색 가능하게 하려면 문서별 인가가 선행되어야 한다. HMAC 가명화는 사용자 권한 인증을 대신하지 않는다.
4. 보류 문구만 개선하는 것과 정답률 향상은 다르다. 재질문 전환율, 실패 원인 비율, 유효 답변 성공률을 별도로 측정해야 한다.
5. 모든 REAL 외부 HCX 질의는 승인된 데이터에 한해 실행한다.

## 검증
```bash
python -m pytest -q tests/test_generic_query_disposition.py
python scripts/verify_release_http.py
```

실제 Gate/DB 회귀 및 숫자형 부분 스팬의 일반화 검증은 로컬 Mac에서 실시해야 한다.
