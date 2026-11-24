# V7 Onepass integration candidate (2026-11-24)

Status: evaluation only; NOT approved for ingestion, query, external API, or deployment. Legacy remains default. `detect_info(..., benchmark_only=True)` is restricted to the benchmark path; other V7 calls fail explicitly. Step2 must implement the real release contract before enabling V7.

## Frozen source

`python -m tools.vendor_v7 --source ~/AegisRAG-unified-v1` extracts named AST definitions only from the original local scripts, including actual postal constants and tokenizer compatibility loader. It compiles and tests the output before saving `starter/v7_frozen.py`. This avoids guessing untracked local source. No Gold, predictions, weights, evaluator imports, or secrets are copied. Generated source hashes are recorded. Once verified, commit the generated source on the personal branch so runtime has no dependency on the old checkout.

## Policy

Raw 510-token / 128 overlap -> validate spans -> existing V6 boundary validation -> V6 structural exclusions -> postal join -> semantic guard. No Combined surname+honorific filter. Added explicit non-postal-field check to postal join; retain review reason and offsets. Keep raw+typed mask union separately, keep excluded candidates and boundary/guard events; never claim union proxy equals actual masking. Guard uses regex/lexical contexts, not another encoder. No retraining or dataset-specific entries. Confidence-based review and independent residual checks are future Step2 work, not implemented here.

## Validation

Run `python -m tools.test_v7_candidate` and scorer self-test before model inference. Full common benchmark reports strict typed, raw, union proxy and review counts using one forward pass per window. Compare with existing historical score rows in TEAM_BENCHMARK_LEDGER.md; deviations must be investigated rather than silently accepted. V7 prior strict F1 targets A187 .996711 / B171 .979522 / Hard403 .908636. No output raw text, no network or DB. Holdout2 stays untouched.
