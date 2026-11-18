# 다음 작업 — 사용자/팀의 입력이 필요한 항목

2026-11-19. 이번 자율 실행은 P5에서 종료했다. 아래 항목은 다음 실행의 선행조건이며 이번 작업에서 자동 실행하지 않았다.

1. 팀원의 실제 `koelectra_ft5` 전체 체크포인트, 파일별 원본 SHA-256, 학습/추론 환경, 모델 공유·재배포 조건을 확보한다. 공개 모델 대체 금지.
2. KLUE의 발신자 SHA-256 및 5~10개 합성 문장 예상 raw span을 확보하고 config의 Transformers 5.21.0과 팀 requirements 5.20.2 차이를 확인한다. 현재 Aegis 환경의 Transformers 5.22.2/torch 2.2.2 조합도 기존 lock과 다르다. 기존 환경을 수정하지 말고 새 격리 환경을 지정하여 원본/새 decoder 동등성을 검증한다.
3. A.X/Veil pinned 로딩·Veil FP32 decoder와 KoE5의 로컬 호환성을 검증할 별도 환경/접근 권한을 확인한다. warm 30회 이상 p50/p95·load time·프로세스별/전체 RSS 실측은 그 이후 수행한다.
4. 운영 DSN과 구분된 폐기 가능한 Postgres 테스트 DB를 지정한다. 현재 migration은 계획만 생성하고 실행을 차단한다. 실 SQL/vector integration과 운영 DB의 읽기 전용 전후 fingerprint 확인은 아직 미검증이다.
5. P5 코드와 합성 검증 결과를 검토한다. 실제 HCX/외부 임베딩/LLM 호출은 별도 명시적 승인·합성 데이터·비용 상한이 필요하다. 이번 경로에는 live transport를 구현하지 않았다.

새 Blind 선정/평가, 운영 프로파일 결정, 서비스 기본값 전환, GitHub push·PR·main 병합은 P6 또는 별도 요청 범위다. 기존 171/187문서 분석 결과를 untouched Blind로 취급하지 않는다.
