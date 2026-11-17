# HCX E2E failure backlog — diagnostics only

Production code and prompts must NOT branch on specific case IDs or Gold document IDs.
Never publish original PII, canonical identifier values or document bodies.

## Status definitions
- CONFIRMED: actual evidence comparison demonstrated a specific failure
- PENDING: evidence is incomplete; avoid calling this a hallucination prematurely
- EXTERNAL: Gate or retrieval, not directly HCX answer selection
- RISK: potential regression not yet measured

| Case | Status | Observation | General mechanism | Next verification |
|---|---|---|---|---|
| Q027 | CONFIRMED | Email-only query: matching H0167 ranked first; HCX cited unrelated H0059 (zero HMAC match) | Generator selected unsupported document and invented an unstated task intent | Same Top-5 A/B, cite matching |
| Q048 | PENDING | Partial-address query, 5 labeled documents in Top-5, zero exact full ADDRESS HMAC matches, HCX insufficient | Exact address vs region match is distinct; Gold may not assert relation | Inspect verified_region_match and actual query semantics |
| Q075 | PENDING | HCX cited H0147 and H0102; both tied to same PERSON and both relevant disputes; Gold has only H0147 | Extra citation may be valid; 2026-12-05 event date is future relative to 2026-11-17 | Verify temporal consistency and human gold completeness |
| Q001/Q015 | EXTERNAL | Gate REVIEW unresolved Veil PERSON | Name-boundary uncertainty, not HCX error | Safe fallback; do not auto-approve |
| Q033 | EXTERNAL | Number span labeled card_number is 3 characters, invalid_number_value | Span/label mismatch; not ban on ID search | Generic numeric span validator |
| Q055/Q065 | EXTERNAL | First Gold rank 41 / 8 | Search candidate ordering issue | Later general retrieval work, separate from answer A/B |

## Experiment and acceptance
1. Baseline vs metadata_guidance on identical Top-5; no filtering, reordering or reranker.
2. If helpful, isolate metadata-only effect.
3. Inspect unchanged successful cases; citation ID matches Gold does not establish claim support.
4. Compare token usage, latency and refusal shifts alongside answer quality.
5. Holdout 88 is development data, not independent blind. Never claim remaining 55 answers are correct without direct HCX review.
6. Keep unresolved cases explicitly, categorize by Gate / retrieval / generation / Gold-quality / chronology; do not create case-ID-specific runtime fixes.
