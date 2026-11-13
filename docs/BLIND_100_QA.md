> **팀 간 비교:** Clean v2의 100문항은 ALL 100을 단일 검증 집합으로 사용합니다. 과거의 dev/validation/challenge 구분은 이번 실험에서 사용하지 않습니다. `--split all`과 `eval_full100.jsonl`을 기준으로 동일 질의/Gold를 평가합니다.

> **2026-11-13 Clean v2 update (recommended for current E2E):** Keep the first raw 171-document Blind ZIP unchanged as the initial benchmark snapshot. A separately downloadable Clean v2 data bundle fixes 7 incorrect date/weekdays in 6 documents, preserving all 1,439 Gold PII character offsets and original document IDs. It also corrects 12 awkward Korean name-mention questions and adds a **separate** 28-document Zero-PII negative-control set covering the other 14 document types. Do **not** mix this negative-control set into the original 171-document Strict F1 denominator. When using Clean v2, replace all `blind_v1` file paths, dataset/namespace values and review-stage names in the steps below with `blind_v2`. The new raw ZIP is named `blind_eval.jsonl.zip` inside the downloaded bundle and keeps `blind_eval.jsonl` as its internal member. Run `scripts/eval_blind_pii.py` independently for the 171 labeled corpus and the 28 empty-Gold controls. Source-world facts beyond calendrical weekday corrections remain subject to human QA; retrieval Hit@K does not prove HCX answer grounding.

# Blind 171 Documents × 100 Natural-Language Questions

This repo stores only benchmark **code**, not raw synthetic PII, questions or Gold.

- Corpus: 171 documents and 1,439 PII span labels in the user-provided ZIP.
- Questions: 100 (32 single-fact, 12 paraphrase, 16 multi-document category, 12 name mention, 8 PII value, 12 clarify, 8 insufficient evidence).
- Splits: all 100, no split. These are question splits, not independent source-corpus splits.
- Gold uses original Bxxxx document IDs. The converter maps IDs using the same namespace-scoped HMAC as the local review intake. Do not compare Bxxxx IDs to stored DB doc IDs.
- User-supplied question and Gold files go under step2_privacy_rag/local_eval/blind_v1/ (git-ignored).

## Gold integrity preflight

    python scripts/prepare_blind_100.py --documents step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip --queries step2_privacy_rag/local_eval/blind_v1/blind_queries_100.jsonl --gold step2_privacy_rag/local_eval/blind_v1/blind_gold_100.jsonl --check-only

## Generate locally mapped test cases

    python scripts/prepare_blind_100.py --documents step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip --queries step2_privacy_rag/local_eval/blind_v1/blind_queries_100.jsonl --gold step2_privacy_rag/local_eval/blind_v1/blind_gold_100.jsonl --namespace blind_v1 --split all --out step2_privacy_rag/local_eval/blind_v1/eval_full100.jsonl

## 1. Provision isolated PostgreSQL on localhost:5544

```bash
# Existing root .env provides POSTGRES_USER/POSTGRES_PASSWORD/RAG_PASSWORD etc.
docker compose --env-file .env -f step2_privacy_rag/compose.blind.yaml up -d

# Copy secret values into a PRIVATE, git-ignored test config.
cp .env step2_privacy_rag/local_eval/blind_v1/blind.env
cat >> step2_privacy_rag/local_eval/blind_v1/blind.env <<'EOF'
DB_HOST=127.0.0.1
DB_PORT=5544
POSTGRES_DB=aegis_blind_test
AEGISRAG_TABLE=rag.aegis_live_docs
AEGISRAG_INDEX_MODE=live
AEGISRAG_LIVE_MIN_ROWS=1
EOF
chmod 600 step2_privacy_rag/local_eval/blind_v1/blind.env
```

The docker compose project name and volume are separated from the original port 5433 database. Do not pass the original root .env to the blind bootstrap command.

## 2. Run original-document Gold audit before any indexing

```bash
python scripts/eval_blind_pii.py \
  --input step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip \
  --report step2_privacy_rag/local_eval/blind_v1/pii_first_report.json
```

Review suspected missed PII; especially documents projected READY but containing a Gold PII literal. A regular expression signal is not a final leak certificate. Keep the FIRST blind test unchanged before tuning.

## 3. Local intake and REVIEW

```bash
unzip -p step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip blind_eval.jsonl \
  > step2_privacy_rag/local_eval/blind_v1/source.jsonl

python scripts/dataset_intake.py stage \
  --input step2_privacy_rag/local_eval/blind_v1/source.jsonl \
  --dataset blind_v1 --id-field id --text-field text --type-field doc_type \
  --stage step2_privacy_rag/local_eval/blind_v1/stage.json

python scripts/dataset_review_ui.py \
  --stage step2_privacy_rag/local_eval/blind_v1/stage.json \
  --env step2_privacy_rag/local_eval/blind_v1/blind.env --port 8781
```

At http://127.0.0.1:8781/ use the one-time terminal token; revise and recheck REVIEW documents locally. **Do not press DB Commit until the initial table has been bootstrapped.** The admin UI uses the selected DB env to write, therefore only point it to blind.env.

## 4. Bootstrap the empty BLIND test DB once

```bash
python scripts/dataset_intake.py export \
  --stage step2_privacy_rag/local_eval/blind_v1/stage.json \
  --output step2_privacy_rag/local_eval/blind_v1/approved.json

python scripts/bootstrap_blind_db.py \
  --input step2_privacy_rag/local_eval/blind_v1/approved.json \
  --env step2_privacy_rag/local_eval/blind_v1/blind.env \
  --confirm BOOTSTRAP_BLIND_TEST_DB
```

The bootstrap code hard-requires dbname=aegis_blind_test, host=localhost, port=5544, empty rag.aegis_live_docs, and the same HMAC key tag. It only accepts approved masked batch records and reuses the canonical KoE5 document embeddings. Subsequent approved documents are added with the admin UI's confirmed incremental UPSERT. This has synthetic CI tests; an actual local 171-document DB bootstrap is not yet executed by this assistant.

## 5. Run retrieval evaluation (DB test mode)

```bash
DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live AEGISRAG_LIVE_MIN_ROWS=1 \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/blind_v1/eval_full100.jsonl --limit 10

DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live AEGISRAG_LIVE_MIN_ROWS=1 \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/blind_v1/eval_full100.jsonl \
  --report step2_privacy_rag/local_eval/blind_v1/search_dev_private.json
```

Use --limit 10 for first smoke tests. The runner distinguishes Gold docs not physically present in the test index from search misses, reports Gate status, Hit@1/5, MRR/Recall@5 on indexable Gold and CPU latency. The report stays owner-readable in local_eval and contains raw synthetic query text.

## 6. Optional paid HCX response audit (only masked documents)

```bash
DB_HOST=127.0.0.1 DB_PORT=5544 POSTGRES_DB=aegis_blind_test \
AEGISRAG_TABLE=rag.aegis_live_docs AEGISRAG_INDEX_MODE=live AEGISRAG_LIVE_MIN_ROWS=1 \
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/eval_chat_mvp.py \
  --cases step2_privacy_rag/local_eval/blind_v1/eval_full100.jsonl \
  --hcx-live --limit 10 \
  --report step2_privacy_rag/local_eval/blind_v1/hcx_full100_private.json
```

Manual check expected_answer, factual correctness, citation support, hallucination, missed privacy labels, unnecessary CLARIFY/INSUFFICIENT_EVIDENCE and per-query latency. Separate metrics for no-evidence/ambiguous questions; don't score these as 0 Hit@K. **After model tuning on dev, do not use challenge 30 for further tuning.**

## 7. End-to-end completion thresholds

- Step1 external blind Strict F1 + Mask Recall; inspect all READY documents with potential Gold miss.
- New documents auto READY vs REVIEW/BLOCKED; each manual correction independently passes unchanged Step1 + Gate.
- No unauthorized raw record in DB/HCX.
- Test DB count and HMAC tag match; re-ingest idempotently skips unchanged docs.
- Indexed Gold coverage and retrieval Hit@K / MRR, multi-positive Recall@K; no false answer for no-evidence cases.
- HCX answer fact support and citation precision manually graded; CPU warm p50/p95 tracked.
- Only after this test passes, plan separate production rollout; never promote the blind test DB directly into production.

Known limitation: The original synthetic Gold includes incorrect dates/weekdays and ambiguous name policies; read the user-provided audit report. New 100 QA questions were checked for source ID/evidence grounding but have not undergone independent two-reviewer semantic validation.
