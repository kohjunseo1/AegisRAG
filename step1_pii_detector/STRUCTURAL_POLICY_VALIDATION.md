# Structural Policy Integration Validation

## Result

**Conditional / incomplete.** The two-rule policy, conservative masking contract, existing regression suite, synthetic reference comparison, and editable installation pass. The required frozen-prediction benchmark and actual Step 2 integration cannot be completed from the files currently available in this repository.

## Implemented

- `standalone_surname_title`: removes a single standalone surname followed by an explicit job title from strict `entities` only.
- `explicit_internal_extension`: removes only a 2–6 digit span inside an explicit `내선:` / `내선번호:` field from strict `entities` only.
- Existing `fuse_labelwise()` and `validate_boundaries()` remain unchanged. The postal validator still runs before the new policy.
- The original post-boundary entities are unioned with refined entities into `mask_intervals`. The policy does not reduce any original covered character.
- Existing status, `review_required`, `issues`, timeouts, failure handling, concurrent inference, window limits, and model routing remain in place. Structural-policy exceptions use the existing fail-closed path.
- `policy_version` and offset-only `structural_policy_events` are returned. Events contain no source text.
- The frozen AX/Veil repository revisions are unchanged:
  - AX: `c0f99b1dc113befefeff1a3091087d50bc3ea635`
  - Veil: `e6e7d20fa9baa43a938f5f6cc780d3f2c9650fb9`

## Automated checks

Executed from `step1_pii_detector/`:

```text
python -m pip install --no-deps -e .   PASS (editable package installation)
python -m unittest discover -s tests -v   PASS: 29 passed, 1 skipped
python -m pip wheel --no-deps --no-build-isolation .   PASS
```

The one skipped test compares against an external reference notebook that is absent from a clean clone. With that notebook available, the same suite ran as 30 passed, 0 skipped. The wheel contains both `pii_detector/structural_policy.py` and `pii_detector/model_manifest.json`. No private model download, conversion, or real-model inference was run for this change.

Coverage includes three/four-syllable names, surname-title whitespace/newline cases, explicit and non-explicit extensions, normal and representative phone numbers, five-digit customer IDs, existing postal expansion and overlap guards, mask-union preservation, unchanged review states, and fail-closed behavior.

## Official Test and Holdout comparison

The user supplied an evaluation bundle under local-only `local_eval/`. All eight files matched its SHA-256/size manifest. The saved labelwise 05 predictions were re-scored with the repository's existing postal validator and then the two policy rules. These are offline postprocessing results, not fresh model inference.

| Dataset | Stage | Strict P | Strict R | Strict F1 | Mask Recall | Unmasked Gold chars |
|---|---|---:|---:|---:|---:|---:|
| Official Test (200 docs) | Frozen 05 predictions | 0.98809 | 0.99657 | 0.99231 | 0.99968 | 3 |
| Official Test (200 docs) | After postal + policy | 0.99061 | 0.99657 | 0.99358 | 0.99968 | 3 |
| Official Holdout (187 docs) | Frozen 05 predictions | 0.96277 | 0.99232 | 0.97732 | 0.99477 | 40 |
| Official Holdout (187 docs) | After existing postal validator | 0.96809 | 0.99781 | 0.98272 | 1.00000 | 0 |
| Official Holdout (187 docs) | After postal + policy | 0.98913 | 0.99781 | 0.99345 | 1.00000 | 0 |

The two structural rules changed 3 Test documents and removed 3 false-positive spans; they removed no true-positive spans. On Holdout, they changed 20 documents and removed 20 false-positive spans; they removed no true-positive spans. Test changes were all `standalone_surname_title`; Holdout changes were 11 `standalone_surname_title` and 9 `explicit_internal_extension`. Holdout is post hoc policy-tuning evidence, not independent blind generalization performance.

`evaluation/offline_regression.py` accepts only exact `--gold` and `--pred` paths, validates the expected row counts, applies the actual repository postal validator when `--include-existing-postal` is supplied, and reports Strict P/R/F1, TP/FP/FN, per-label counts, mask recall/unmasked Gold characters, policy changes, and postprocessing-only p50/p95. Saved 05 predictions do not carry detector status, so the script reports review rate as unavailable. It does not report model inference latency.

No document-specific exception was added. The summary JSON reports are kept outside Git at `/tmp/official_test_policy_summary.json` and `/tmp/official_holdout_policy_summary.json`; raw Gold and prediction files remain local-only and are excluded by the repository `.gitignore`.

## Step 2 handoff

The repository has no Step 2 pseudonymization implementation. The detector response and README define the contract: downstream de-identification must use `mask_intervals`, route `REVIEW_REQUIRED` for review, and stop on `BLOCKED`. A mock pseudonymizer test applies those intervals and confirms even Strict spans removed by policy are masked. End-to-end integration against the future Step 2 implementation remains pending.

## Existing postal validation is not duplicated

The new policy module has exactly two rules: `standalone_surname_title` and `explicit_internal_extension`. It has no address or postcode rule. `detector.py` still calls the original `validate_boundaries()` before `refine_spans()`, and regression tests continue to exercise its conditional postal expansion, non-postal field rejection, and overlap protection.
