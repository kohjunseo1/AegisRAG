# AegisRAG 신규 데이터셋 수집 → REVIEW → Live DB 증분 적재

현재 구현: 최상위 JSONL 필드 입력, A.X/Veil/Privacy Gate를 통한 로컬 자동 분류, 로컬 서명 스테이징, 검토자가 수정한 원문의 동일 Gate 재검증, 승인 문서만 기존 Live DB에 반영.

이 시스템은 새로운 데이터셋에도 동일한 처리를 재사용하되, **모든 문서가 READY라는 보장은 없습니다**. 검토 파일과 수정 원문은 로컬에서만 관리합니다. 특히 REVIEW 원본은 git-ignored local_eval 아래 파일에 평문으로 저장되므로 디스크 암호화와 파일 접근 통제가 별도로 필요합니다.

## 1. 임의 컬럼 JSONL 입력과 자동 분류

예: 원본 source.jsonl 행은 {"uuid":"001","body":"문서 내용","title":"공지 제목","kind":"공지"} 형식입니다. 다른 열 이름이라면 옵션으로 바꿉니다.

    python scripts/dataset_intake.py stage \
      --input /path/to/source.jsonl --dataset corp2 \
      --id-field uuid --text-field body \
      --title-field title --type-field kind \
      --stage step2_privacy_rag/local_eval/corp2/stage.json

    python scripts/dataset_intake.py status \
      --stage step2_privacy_rag/local_eval/corp2/stage.json

DATASET namespace와 원본 ID를 고정 키의 도메인 분리 HMAC으로 변환하므로 기존 H0025 등과 충돌하지 않고, 원본 ID가 답변의 인용에 노출되지 않습니다.
출력에는 개인정보 없이 READY / REVIEW_PENDING / BLOCKED 수와 검토 대상 문서 ID가 나옵니다.
원본 및 보호된 문서는 GitHub에 업로드하지 않습니다.

## 2. 수동 검토와 실제 재검증

REVIEW에 걸린 문서는 반드시 검토자가 로컬의 원본을 직접 확인하고 미보호 PII를 수정한 원문 파일을 작성해야 합니다. 단순 상태값을 HUMAN_APPROVED로 바꾸는 조작은 허용하지 않습니다.

    # status 출력의 review_ids 또는 8781 화면에서 실제 생성 ID 확인
    python scripts/dataset_intake.py review \
      --stage step2_privacy_rag/local_eval/corp2/stage.json \
      --doc-id 'REVIEW_ID_FROM_STATUS' \
      --revised-source-file /path/to/revised_source.txt \
      --reviewer localreviewer

수정된 원문을 **동일 A.X + Veil + Privacy Gate에 다시 통과**시킵니다. 여전히 REVIEW/BLOCKED면 승인하지 않습니다.
승인되면 로컬 stage에 검토 ID, 시간, 수정 원문 해시가 기록됩니다.
기존 관리자 UI(로컬 8771)와는 별도입니다. 신규 8781 UI에서는 원본 확인·수정 후 같은 재검증 및 DB 반영을 한 화면에서 수행할 수 있습니다. [UI 실행 설명](DATASET_REVIEW_UI.md).

## 3. 승인된 문서만 DB 반영

    python scripts/dataset_intake.py export \
      --stage step2_privacy_rag/local_eval/corp2/stage.json \
      --output step2_privacy_rag/local_eval/corp2/approved.json

    python scripts/aegisrag.py ingest-live \
      --input step2_privacy_rag/local_eval/corp2/approved.json \
      --confirm rag.aegis_live_docs

검증된 AUTO_READY 또는 HUMAN_APPROVED만 내보냅니다.
기존 ingest-live의 key tag 검사, 잔여 PII 패턴 검사, 변경 문서 재임베딩,
트랜잭션 upsert를 사용하므로 **기존 187문서를 재처리하지 않습니다**.
웹 챗봇은 다음 질문에서 동일 Live 테이블을 새로 조회합니다.

## 4. 남은 E2E 완성 조건

- 기존 관리자 8771 UI와 신규 8781 도구 통합 또는 역할 정리; 현재 8781의 실제 모델·DB 연동 실측 검증, 사용자 권한·감사로그 강화
- 블라인드 합성 신규 문서 테스트: Strict PII F1, Mask Recall, False READY, REVIEW 회수율
- 별도 신규 질의의 Search Hit/Recall·답변 사실성/인용 정확도·안전성 평가
- CSV/PDF 등 확장 입력 어댑터, 문서 출처/버전/접근 권한 관리
- 일반적인 Dense + Lexical / BM25 계열 검색 및 랭킹 비교 실험, 동명이인 연결 설계
