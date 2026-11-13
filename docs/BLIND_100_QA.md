# Blind 171 Documents × 100 Natural-Language Questions

This repo stores only benchmark **code**, not raw synthetic PII, questions or Gold.

- Corpus: 171 documents and 1,439 PII span labels in the user-provided ZIP.
- Questions: 100 (32 single-fact, 12 paraphrase, 16 multi-document category, 12 name mention, 8 PII value, 12 clarify, 8 insufficient evidence).
- Splits: dev 40, validation 30, challenge 30. These are question splits, not independent source-corpus splits.
- Gold uses original Bxxxx document IDs. The converter maps IDs using the same namespace-scoped HMAC as the local review intake. Do not compare Bxxxx IDs to stored DB doc IDs.
- User-supplied question and Gold files go under step2_privacy_rag/local_eval/blind_v1/ (git-ignored).

## Gold integrity preflight

    python scripts/prepare_blind_100.py --documents step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip --queries step2_privacy_rag/local_eval/blind_v1/blind_queries_100.jsonl --gold step2_privacy_rag/local_eval/blind_v1/blind_gold_100.jsonl --check-only

## Generate locally mapped test cases

    python scripts/prepare_blind_100.py --documents step2_privacy_rag/local_eval/blind_v1/blind_eval.jsonl.zip --queries step2_privacy_rag/local_eval/blind_v1/blind_queries_100.jsonl --gold step2_privacy_rag/local_eval/blind_v1/blind_gold_100.jsonl --namespace blind_v1 --split dev --out step2_privacy_rag/local_eval/blind_v1/eval_dev.jsonl

## Run AFTER local review and isolated test-DB ingestion

    python scripts/eval_chat_mvp.py --cases step2_privacy_rag/local_eval/blind_v1/eval_dev.jsonl --limit 10

HCX actual API calls (only masked data, paid and explicitly approved):

    AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES python scripts/eval_chat_mvp.py --cases step2_privacy_rag/local_eval/blind_v1/eval_dev.jsonl --hcx-live --limit 10

**Important:** The live upsert requires an initialized nonempty table and HMAC key consistency. Provision an isolated PostgreSQL test database and appropriate initial seeded snapshot, never use the original 187-row production DB for first-run testing. The actual blind integration is not verified until run locally.

Other limitations: Current eval_chat_mvp measures retrieval hits, HCX decision and citation matches, NOT independent factual correctness of a generated answer. Review answers manually against the expected fact and original sanitized evidence; record separate human accuracy, unsupported claims, privacy leak and p95 latency. Do not use the 30 challenge questions to tune the system.
