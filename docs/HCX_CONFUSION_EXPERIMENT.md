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

## Rate-limit diagnosis / Fail-fast (2026-11-18)

The HCX client retains the same model, system prompt, temperature and output budget. In this experiment branch:

- HTTP 429 captures an allowlisted numeric provider subcode (if available) and safe rate-limit headers; no raw response body, query, document text or secrets are logged.
- A 429 is flushed to the private JSONL report, then the experiment immediately stops with CONF_RATE_LIMIT_STOP; no retries.
- rate_limit_diagnostics records request_bytes, question_chars, document_chars, document_count, max_completion_tokens, and optional provider_code / rate limit counters / reset times.
- If the provider returns neither a subcode nor rate limit headers, the cause remains unknown.
- The failed 15-call and 2-call runs have zero valid HCX answer observations. The separate successful short synthetic request does not establish the limit of larger requests.

Offline tests (no HCX or DB):

    python -m unittest discover -s tests -p 'test_hcx_confusion_sensitivity.py' -v

Existing detached worktree: inspect local tracked changes before updating, never delete private local_eval files:

    cd ~/AegisRAG-confusion
    git status --short
    git fetch origin experiment/confusion-sensitivity-20261117
    git switch --detach origin/experiment/confusion-sensitivity-20261117

If tracked edits exist, preserve them first. For a paid --live retry, use a new private report filename, and only after confirming authorization for the masked corpus. If a rate limit is encountered, inspect the safe diagnostics instead of retrying repeatedly.
## Single-document addition ablation (diagnostic only)

When the Gold document is retrieved but HCX cites non-Gold documents, use the generic `single_additions` condition set. This is **not a production ranking rule**; Gold is used only to create offline counterfactuals, and Gold labels are never sent to HCX.

For Q027, with its current actual Top-5, the plan is:

| Condition | Documents |
|---|---|
| gold_only | H0167 |
| gold_plus_H0059 | H0167, H0059 |
| gold_plus_H0159 | H0167, H0159 |
| gold_plus_H0010 | H0167, H0010 |
| gold_plus_H0060 | H0167, H0060 |
| actual_top5 | H0167, H0059, H0159, H0010, H0060 |

This exact list is an **example from Q027's current Top-5**, not a hardcoded case. The implementation generates additions for every non-Gold Top-5 document, whether or not it was annotated confusable. Original ranking order is preserved within each condition. Duplicate document subsets are reused, not called twice.

Plan only, without outbound calls:

    cd ~/AegisRAG-confusion
    python scripts/eval_hcx_confusion_sensitivity.py \
      --cases ~/AegisRAG-hcx-ab/step2_privacy_rag/local_eval/e2e_pilot_17.jsonl \
      --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
      --qids Q027 --trials 1 --condition-set single_additions

Live execution is optional and **requires explicit approval of masked source material** as well as the provider cost budget. Use `--live` plus a new unique private `--out` path and `AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES`; there can be six API requests for Q027. On HTTP 429 it stops after the first failure. Do not overwrite earlier reports.

The primary metric is Gold doc citation vs non-Gold citation **for identical question/system settings**. A change in citation choice is evidence of sensitivity, but does not prove what causal mechanism changed (document order, length or interaction can also matter). Inspect the locally stored answer text and claims before declaring semantic correctness.
## Structured Evidence Audit A/B (isolated, 2026-11-18)

**Purpose.** Q027's retrieved Top-5 contains H0167 (Gold) as rank 1, but HCX has cited H0059 for an unrelated VOC question. In the 6-condition single-addition experiment, adding H0059 to H0167 resulted in INSUFFICIENT_EVIDENCE; other individual additions still cited H0167. This establishes **input sensitivity in observed responses**, not reliable causality or stable failure frequency. H0059 was *not* annotated confusable in the Gold: it is a newly discovered interfering **non-Gold** document.

The A/B mode compares four conditions with the SAME original Top-5 and HMAC/Gate/retrieval pipeline:

| Condition | Documents | HCX prompt/schema |
|---|---|---|
| pair_baseline | Gold found in actual Top-5 + CLI-selected non-Gold doc | Current baseline |
| pair_audit | Identical pair | Structured evidence review + answer in **one** call |
| top5_baseline | Actual Top-5 original ranking | Current baseline |
| top5_audit | Identical actual Top-5 | Structured evidence review + answer in **one** call |

IMPORTANT: H0059 is passed by CLI for Q027; **no special doc IDs, names, financial-event types, or Gold labels are hardcoded into model logic**. Gold only creates a counterfactual diagnostic document subset and stays local. HCX-007, temperature 0, reasoning_effort none, output cap 1024, and original masked documents/order are retained. The audit condition adds a prompt and JSON schema; that extra structure can itself affect cost, latency, and output behavior.

Audit output has the normal decision/answer/citations plus per-document verdict (SUPPORTS, WRONG_TASK, WRONG_ENTITY, WRONG_TIME, INSUFFICIENT) and a short supporting_quote for SUPPORTS. Locally, the code checks each quote is an exact substring of the **transmitted masked document** and that the model's citations agree with its own SUPPORTS labels. **No quote text is written to diagnostic JSONL or console**; only IDs, verdict, quote_valid and consistency flags. A matching quote is necessary but not sufficient for factual relevance; verdicts are model guesses, NOT the model's faithful internal reasoning.

Update existing detached worktree after preserving tracked edits:

    cd ~/AegisRAG-confusion
    git status --short
    git fetch origin experiment/confusion-sensitivity-20261117
    git switch --detach origin/experiment/confusion-sensitivity-20261117

Offline tests (NO HCX calls):

    python -m unittest discover -s tests -p 'test_hcx_confusion_sensitivity.py' -v
    python -m unittest discover -s tests -p 'test_hcx_evidence_audit.py' -v

Plan-only Q027 (NO HCX calls; 4 planned calls):

    python scripts/eval_hcx_confusion_sensitivity.py \
      --cases ~/AegisRAG-hcx-ab/step2_privacy_rag/local_eval/e2e_pilot_17.jsonl \
      --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
      --qids Q027 --trials 1 --condition-set audit_ab --focus-doc-id H0059

Optional paid live call only after authorization for the masked corpus and confirming the shared token budget:

    export AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES
    python scripts/eval_hcx_confusion_sensitivity.py \
      --cases ~/AegisRAG-hcx-ab/step2_privacy_rag/local_eval/e2e_pilot_17.jsonl \
      --gold ~/AegisRAG/step2_privacy_rag/local_eval/holdout_retrieval_queries_gold.jsonl \
      --qids Q027 --trials 1 --condition-set audit_ab --focus-doc-id H0059 \
      --live --out step2_privacy_rag/local_eval/q027_evidence_audit_ab_v1.jsonl

Use a fresh report filename each run. The script stops at the first 429 and preserves partial results. Store output locally, do not share raw private answers. The existing review script summarizes decisions and cited IDs:

    python scripts/review_hcx_confusion.py \
      --report step2_privacy_rag/local_eval/q027_evidence_audit_ab_v1.jsonl

**Interpretation:** Compare Gold citations, wrong-task answers, false abstention, quote_valid flags, citation_verdict_consistent and actual claims in privately stored masked answers. A single successful A/B does not justify release. Replicate on Q075/Q048 and a predeclared untouched evaluation set; check added latency and tokens before considering adoption.
## Interpretation

- Gold-only correct, Gold+Confusable incorrect: likely confusable interference, but added length and positions might also matter.
- Gold+Confusable correct, Top-5 incorrect: other distractors or context effects.
- Gold-only wrong: possible document interpretation/generation difficulty, insufficient subset of Gold, or ambiguous question; check manually.
- No difference in candidate IDs: cannot infer an injection effect. A reused result is not an independent sample.
- Citation inclusion is NOT factual correctness. Compare actual claims and omissions against the private masked source evidence.
- In production, do not use Gold to pick documents. This experiment is strictly diagnostic.
