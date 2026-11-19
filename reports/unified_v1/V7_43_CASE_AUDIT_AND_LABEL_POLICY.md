# A.X V7 — 43-case error audit and labeling policy (2026-11-20)

The 43 JSONL rows were sampled from an exposed, synthetic, 403-document test suite. Source passages are not included in this GitHub document.

## What the sample actually measures

| Error bucket | Full hard403 count | Exported sample |
| --- | ---: | ---: |
| PERSON strict no-overlap | 36 | 8 |
| PERSON strict boundary mismatch | 57 | 8 |
| PERSON unmatched false positive | 57 | 8 |
| ADDRESS strict no-overlap | 3 | 3 |
| ADDRESS strict boundary mismatch | 16 | 8 |
| ADDRESS unmatched false positive | 9 | 8 |
| TOTAL | 178 | 43 |

These counts are NOT a 43-row training dataset. The case exporter selects the first N errors per category; it is neither stratified nor representative of all failure types. Unmatched FP is a subset of all FP. Do not use these sample frequencies directly as a class-sampling ratio.

## Observed PERSON patterns

- One-character names, given-name-only references, two-character names, compound family names and internal spaces.
- Attached Korean case markers, honorifics and titles; contiguous job role + name.
- Names adjacent to bank/phone/card fields and repeated name mentions in the same document.
- English and Korean aliases with differing token boundaries.
- Company/product/brand names that look person-like.
- Some passages explicitly call a phrase a product or organization but the Gold labels it PERSON; other likely personal mentions appear absent from Gold even when detected. This is a possible annotation-policy conflict, NOT proof the Gold or detector is wrong.

## Observed ADDRESS patterns

- Gold spans include ZIP codes that the detector omits. Agree on whether five-digit ZIP belongs inside ADDRESS.
- Lot-number and street formats, building/apartment/room suffixes, parentheses, no-space and multiple nearby addresses.
- Two sampled Gold spans contain only the generic word "주소", and another contains a locality without numeric location. These deserve manual adjudication instead of blind supervision.
- Do NOT automatically call a business/street address non-PII. The service's document protection policy may still require masking sensitive contact locations.

## Required annotation decisions before supervised learning

1. PERSON: define personally identifying mentions vs aliases, family names only, job roles, companies and brand/product names. Context matters more than character count.
2. ADDRESS: define personally identifying, partial, business and generic location mentions, and ZIP inclusion.
3. Ensure all positive Unicode start/end offsets correspond to the intended entire mention and that the tokenizer can produce the same boundaries.
4. Mark disputed cases as annotation_conflict; never train them as automatic positive or verified O until adjudicated.
5. Build NEW synthetic templates and contrast examples from these concepts. The exposed 43 sentences themselves are diagnostic only, not train or new Dev material.

## Code safeguards implemented

- Continue the fine-tuned 37-tag A.X checkpoint, not a fresh head.
- Partially annotated seven-type rows mask unannotated tokens as loss=-100, preserving unknown 37-type regions.
- Explicit verified_o_spans allow supervised O only on deliberately audited non-PII substrings. Gold/O overlap and substring mismatches fail validation.
- Token-boundary mismatch on positives fails preflight instead of silently merging a suffix into a name or address.
- Student-versus-frozen-teacher KL helps retain the old 37-way behavior, but cannot prove no forgetting.
- Old Official Dev may be replay data; old test/locked/holdout remain excluded. The public 403 set and inspected B are regression only, not new blind evidence.
- The bootstrap generator is a data CONTRACT test, not sufficient evidence of robustness. Build and review independently templated train/new Dev/holdout before real V7 selection.

## Release rule

An eventual V7 must be compared with frozen V6 by all-seven strict FN/FP, completely and partly uncovered Gold, error class/label, annotation conflict rate, false READY and typed HMAC integrity, latency p50/p95, RSS and the same 88-query search/Confuse metrics. Never assume an output-side check can undo an unmasked-PII transfer to an LLM.
