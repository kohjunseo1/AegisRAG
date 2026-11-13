# 새로운 Blind JSONL + Gold PII 평가 (운영 DB 미접촉)

`blind_eval.jsonl.zip`처럼 문서 원문과 `entities`의 시작·끝 위치를 제공하는 **처음 보는 데이터셋**에 대해, 먼저 Step1의 Strict P/R/F1·Mask 문자 Recall과 Step2의 Gate READY/REVIEW/BLOCKED 분포를 측정합니다.

기존 Holdout로 모델·규칙을 더 튜닝한 뒤 이 데이터에서 **첫 번째 점수**를 내는 것은 피하세요. 최초 Blind 결과를 기록하고, 실패를 분석한 후 수정한 모델은 새 데이터를 기준으로 다시 검증해야 합니다.

## 실행 (가명처리 결과 로컬에만, DB 쓰기 없음)

```bash
# 본인 컴퓨터의 ZIP 경로 지정 (ZIP을 풀 필요는 없음).
TOKENIZERS_PARALLELISM=false python scripts/eval_blind_pii.py \
  --input /path/to/blind_eval.jsonl.zip \
  --report step2_privacy_rag/local_eval/blind_v1/pii_report.json
```

출력: `BLIND_SCHEMA_OK`, `BLIND_PROGRESS`, `BLIND_PII_METRICS` (집계만), 개인 로컬 보고서.

- `strict`: 라벨과 시작·끝 위치가 모두 일치해야 TP.
- `step1_mask_gold_character_recall`: Gold 문자 중 Step1 마스크 영역이 포함한 비율. **최종 가명처리의 실제 보호율과는 다릅니다.**
- `ready_docs_with_suspected_literal_residual`: READY 가명처리 출력에 Gold 원문 문자열이 그대로 남은 것이 의심되는 문서 수. 짧은 인물 이름이 다른 단어 속에 출현할 수 있어서 오탐성 신호이며, 반드시 로컬 원본으로 수동 확인해야 합니다.
- 각 문서는 결과 파일에서 원본 ID 대신 해시 접두사로 표시합니다. Gold 원문·토큰·모델 출력은 보고서에 기록하지 않습니다.

**DB 적재 승인 판단:** 문서의 READY는 무오류의 보증이 아닙니다. 이 Blind 데이터는 Gold가 있으므로, 오탐과 미탐/잔여문자를 확인한 후에만 테스트 인덱스에 적재하세요. 특히 새 데이터의 검증을 운영 187문서 DB에 바로 수행하지 마세요.

## E2E 테스트 후속 단계

1. 원본 ZIP을 로컬 `local_eval`에 안전하게 압축 해제한 뒤 `dataset_intake.py stage --dataset blind1 --id-field id --text-field text --type-field doc_type`로 분류. 이 단계는 `"문서 유형: ..."`을 앞에 붙이므로 **Gold 원본 오프셋은 stage 출력에 그대로 적용되지 않습니다**.
2. 스테이징 READY / REVIEW를 **별도 테스트 PostgreSQL 인스턴스**에 적재하고 실제 관리자 UI의 승인·검색 연결 검증. 현재 `ingest-live`는 기본적으로 `rag.aegis_live_docs`를 대상으로 하므로 테스트용 DB 설정을 먼저 확인하세요.
3. 이 ZIP에는 **질의·검색 Gold·모범 답변이 없습니다**. 문서로부터 새 자연어 질문과 정답 문서 ID, 정답 사실/인용 범위를 따로 마련해 Hit@K와 HCX 사실성·인용 정확도 평가를 해야 합니다.
4. 이 파일이 기존 187문서와 완전히 독립인지 여부는 기존 원본과 ID·본문 fingerprint를 비교해야 확정할 수 있습니다. 외부 제공 데이터라는 이유만으로 독립성이 입증되지는 않습니다.
