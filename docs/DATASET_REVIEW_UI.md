## 별도 로컬 관리자 검토 웹 UI (포트 8781)

현재 프로젝트의 다른 8771 관리자 서비스와 독립적입니다. 이 저장소에 포함된 stage 포맷을 직접 읽고 수정·재검증·증분 적재를 수행합니다.

1. 먼저 dataset_intake.py stage 명령으로 signed stage.json을 준비합니다.
2. 다음 명령으로 관리 서버를 로컬 루프백에서만 시작합니다.

    python scripts/dataset_review_ui.py \
      --stage step2_privacy_rag/local_eval/corp2/stage.json \
      --port 8781

3. 터미널의 REVIEW_ACCESS_TOKEN을 브라우저 http://127.0.0.1:8781/ 로그인 칸에 붙여 넣습니다.
4. REVIEW_PENDING/BLOCKED 항목을 클릭하여 원문을 읽고 직접 수정합니다.
5. 검토자 ID 입력 후 재검증·승인 버튼을 누릅니다. 동일 Step1 + Gate를 통과해야 승인됩니다.
6. rag.aegis_live_docs를 확인란에 정확하게 입력한 다음 승인 문서 DB 반영 버튼을 누릅니다.
7. 웹 챗봇 8768은 동일 DB를 쓰므로 새 질문부터 새 문서를 검색할 수 있습니다.

서버는 127.0.0.1만 바인딩하고, 모든 민감 API 요청은 임시 접근 토큰을 검증합니다. POST는 Origin을 확인하며 원문이나 요청 URL을 로그에 남기지 않습니다. 전체 서비스는 로컬 단일 운영자용이고, 팀용 인증·권한·동시 접속·감사 시스템을 구현한 것은 아닙니다.
