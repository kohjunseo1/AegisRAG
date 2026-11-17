# Natural-language MVP evaluation (separate from official Holdout)

Use this evaluation to distinguish **Gate failures, retrieval misses, and HCX answer/citation failures**.
The included cases are **development smoke tests based on already inspected synthetic documents**.
They are NOT a new blind/independent test set.

## Reproduce

```bash
# Generate example queries locally (the file is git-ignored).
python scripts/eval_chat_mvp.py --write-example

# Start from repo root with live index / HF token already configured.
# No HCX API calls, no database writes:
python scripts/eval_chat_mvp.py

# Actual full chat decisions (uses paid HCX-007 API, masked text only):
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/eval_chat_mvp.py --hcx-live
```

The local default fixture is `step2_privacy_rag/local_eval/mvp_natural_questions.jsonl`.
Each JSONL row has `case_id`, `question`, optional `category`,
and optional `relevant_doc_ids` (a **human-verified** list).

- When a reference doc ID exists, the runner reports whether that ID appears in Top-1/Top-5.
- Without a manually verified reference, it reports only Gate state, candidate IDs, and decision.
- With `--hcx-live`, it also reports selected citation IDs and whether a labeled document was cited. **It does not auto-judge answer factuality.**
- Cases are never sent to HCX when the Gate blocks them.
- The runner never writes or prints the raw questions, documents, or HCX response text. User-visible answers are tested separately in the web UI.
- The `MVP_EVALUATION_SUMMARY` latency p50/p95 excludes model preload, includes HCX only in live mode.

## Test actual unseen queries (important)

Create a new `/path/to/new_questions.jsonl` consisting of **20–30 human-written queries that were not used to tune this model**.
Include roughly ten questions about known indexed documents, five paraphrases/variants,
five ambiguous or incomplete questions, and five queries whose answer may not exist.
Verify any labeled `relevant_doc_ids` against the original local synthetic corpus, not the retrieved Top-5.
Do not assign Gold from the system output. Do not print original customer content in reports.

```bash
python scripts/eval_chat_mvp.py --cases /path/to/new_questions.jsonl
AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES \
python scripts/eval_chat_mvp.py --cases /path/to/new_questions.jsonl --hcx-live
```

**Targeted ablation:** The new general document-topic match boosts `경조사` queries toward `부고` records when no identity matching exists. Compare with frozen V6:

```bash
AEGISRAG_TOPIC_BOOST=0 AEGISRAG_QUERY_RECOVERY=0 python scripts/eval_chat_mvp.py
AEGISRAG_TOPIC_BOOST=1 AEGISRAG_QUERY_RECOVERY=1 python scripts/eval_chat_mvp.py
AEGISRAG_TOPIC_BOOST=0 AEGISRAG_QUERY_RECOVERY=0 python scripts/verify_v6_e2e.py --eval-raw
AEGISRAG_TOPIC_BOOST=1 AEGISRAG_QUERY_RECOVERY=1 python scripts/verify_v6_e2e.py --eval-raw
```

Do **not** reuse old 77/88 or 83/88 numbers as the score after changing ranking.
Original Holdout has already been investigated repeatedly and cannot be represented as a blind test.
Failure to find H0025 with topic boost enabled means the user should inspect the local masked H0025
and the Top-5 candidate list, rather than relaxing the Privacy Gate or hardcoding a Gold doc ID.

For accurate E2E assessment, also manually grade: answer correctness, citation support,
unsupported assertions, appropriate abstention/clarification, privacy leakage, and p95 latency.

**Known-label warning:** The example H0025 labels mark a *known relevant* notice, not every relevant notice. Do not interpret absence of H0025 in Top-5 as proof that no valid family-event document was returned. Expand manual Gold if comparing recall for broad category queries.

**Online Gate fallback:** `AEGISRAG_QUERY_RECOVERY=1` enables only the anchored one-person + safe information/document-intent retry after `GATE_NOT_READY`. Name alone must pass the full original Gate and yield exactly one PERSON HMAC token, otherwise original REVIEW stands. Disable it with `AEGISRAG_QUERY_RECOVERY=0` to reproduce frozen V6. No LLM is used for normalization.
