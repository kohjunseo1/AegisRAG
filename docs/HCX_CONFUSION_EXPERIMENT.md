# HCX Confusion Sensitivity — diagnostic only

## Purpose

The question and HCX baseline settings are identical for all conditions. Only the masked documents change:

| Condition | Evidence |
|---|---|
| gold_only | Gold-labeled documents among the ACTUAL retrieved Top-5 |
| gold_confusable | Same Gold documents plus annotated confusable documents among ACTUAL Top-5 |
| actual_top5 | All 5 real documents, original order |

Gold is strictly evaluation-only. Neither Gold labels, confusable labels nor Gold notes go to HCX. There are no production retrieval, DB, Gate, HMAC or ranking modifications.

When Gold+Confusable and actual Top-5 are identical, one response is reused (not billed twice). When there is no confusable in Top-5, the injection condition is skipped. When no Gold is in Top-5, both Oracle conditions are skipped. Gold-only here is an in-Top-5 Oracle, not all Gold in the 187-document corpus.

## Isolated worktree

    cd ~/AegisRAG
    git fetch origin experiment/confusion-sensitivity-20261117
    git worktree add --detach ../AegisRAG-confusion origin/experiment/confusion-sensitivity-20261117
    cd ../AegisRAG-confusion
    ln -s ../AegisRAG/.env .env

Do not repeat worktree creation/linking if the directory or link already exists. Use your previously installed AegisRAG Python environment.

    export AEGISRAG_TABLE=rag.aegis_live_docs
    export AEGISRAG_INDEX_MODE=live
    export AEGISRAG_LIVE_MIN_ROWS=187
    export AEGISRAG_TOPIC_BOOST=0
    export AEGISRAG_HCX_EVIDENCE_MODE=baseline

Unit tests, zero HCX/DB:

    python -m unittest discover -s tests -p 'test_hcx_confusion_sensitivity.py' -v

## Plan only, ZERO HCX calls

    python scripts/eval_hcx_confusion_sensitivity.py \
      --cases ~/AegisRAG-hcx-ab/step2_privacy_rag/local_eval/e2e_pilot_17.jsonl \
      --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl

Default QIDs: Q052 Q075 Q027 Q048 Q062. Plan loads local models and reads DB but does not call HCX. Count printed as planned_hcx_calls; expected around 10 given prior Top-5. Actual count depends on the live index.

## Optional live diagnostic (actual paid HCX calls)

Only with explicitly approved masked input documents:

    export AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES
    python scripts/eval_hcx_confusion_sensitivity.py \
      --cases ~/AegisRAG-hcx-ab/step2_privacy_rag/local_eval/e2e_pilot_17.jsonl \
      --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
      --trials 1 --live \
      --out step2_privacy_rag/local_eval/confusion_first_v1.jsonl

The report is exclusive-created, private, and never overwritten. Results are appended after each condition. A failed run may yield a partial report; use a new output filename to retry specific QIDs.

## Offline scoring and locally scrubbed answer comparison

    python scripts/review_hcx_confusion.py \
      --report step2_privacy_rag/local_eval/confusion_first_v1.jsonl

    python scripts/review_hcx_confusion.py \
      --report step2_privacy_rag/local_eval/confusion_first_v1.jsonl \
      --show-answers --qids Q052 Q075 Q027 Q048 Q062

Review output for any remaining private information before sharing. Token and common PII redaction is defense-in-depth, not a certification of safety.

## Interpretation

- Gold-only correct, Gold+Confusable incorrect: likely confusable interference, but added length and positions might also matter.
- Gold+Confusable correct, Top-5 incorrect: other distractors or context effects.
- Gold-only wrong: possible document interpretation/generation difficulty, insufficient subset of Gold, or ambiguous question; check manually.
- No difference in candidate IDs: cannot infer an injection effect. A reused result is not an independent sample.
- Citation inclusion is NOT factual correctness. Compare actual claims and omissions against the private masked source evidence.
- In production, do not use Gold to pick documents. This experiment is strictly diagnostic.
