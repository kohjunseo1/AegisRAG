# V7 Onepass integration candidate (2026-11-24)

Status: evaluation only; NOT approved for ingestion, query, external API, or deployment. Legacy remains default. `detect_info(..., benchmark_only=True)` is restricted to the benchmark path; other V7 calls fail explicitly. Step2 must implement the real release contract before enabling V7.

## Frozen source

`python -m tools.vendor_v7 --source ~/AegisRAG-unified-v1` extracts named AST definitions only from the original local scripts, including actual postal constants and tokenizer compatibility loader. It compiles and tests the output before saving `starter/v7_frozen.py`. This avoids guessing untracked local source. No Gold, predictions, weights, evaluator imports, or secrets are copied. Generated source hashes are recorded. Once verified, commit the generated source on the personal branch so runtime has no dependency on the old checkout.

## Policy

Raw 510-token / 128 overlap -> validate spans -> existing V6 boundary validation -> V6 structural exclusions -> postal join -> semantic guard. No Combined surname+honorific filter. Added explicit non-postal-field check to postal join; retain review reason and offsets. Keep raw+typed mask union separately, keep excluded candidates and boundary/guard events; never claim union proxy equals actual masking. Guard uses regex/lexical contexts, not another encoder. No retraining or dataset-specific entries. Confidence-based review and independent residual checks are future Step2 work, not implemented here.

## Validation

Run `python -m tools.test_v7_candidate` and scorer self-test before model inference. Full common benchmark reports strict typed, raw, union proxy and review counts using one forward pass per window. Compare with existing historical score rows in TEAM_BENCHMARK_LEDGER.md; deviations must be investigated rather than silently accepted. V7 prior strict F1 targets A187 .996711 / B171 .979522 / Hard403 .908636. No output raw text, no network or DB. Holdout2 stays untouched.


## Review-context refinement (pending regression, 2026-11-24)

The original verified tag is `step1-v7-validated-20261124` (759ea76). The new `starter/v7_context_validated.py` extends the frozen ValidatedOnepass contract: resolve uncertainty events using existing surname/title and obituary evidence, narrow structured dialogue labels, URL hostname, service contact and amount placeholder context. Keep compact Korean surname/title expressions, ambiguous name suffixes and other identifiers pending unless sufficiently supported. No typed edits, no protection offset removal, no Gold in runtime, no additional model. A187 is the primary development corpus, B171 is supplementary. Hard403 remains the historical frozen baseline, not a policy-tuning objective; do not present its old review count as a new measurement. Holdout2 untouched.

Run `python -m tools.benchmark_v7_context_validated --out <private-output>` for live A187/B171 regression. It tests raw and typed parity, exact old review counts, protection equality, audit/review accounting and no Gold overlap among resolved events. Results pending. Old experiment scripts and old evaluation path remain for reproducibility; final team-facing cleanup after Step2. No ingestion/query or external release enabled.

## Model distribution

Current runtime loads a complete local fine-tuned ModernBERT token classification checkpoint, not an adapter and not Drive at inference time. Preserve full tokenizer/config assets and weight SHA256 978a1c8a7100d2c2411ce797c72cb08b235dcf150f77d3003e4f05881bd8bd1a. Suggested distribution: private Hugging Face model repository in PII-detect organization, pending user approval/upload and upstream license/access verification. Download a pinned revision in provisioning, verify hashes, then use local_files_only=True. Never upload data, predictions, private audits, keys, optimizer state or other training artifacts. The weights alone do not implement the Python privacy policy. Keep local + Drive backup until remote snapshot verified.
