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

| Dataset | Gold rows present locally | Required frozen 05 labelwise predictions | Baseline vs. combined score |
|---|---:|---|---|
| Official Test | 200 | Not available | Not run |
| Official Holdout | 187 | Not available | Not run |

No saved 05 prediction JSONL or benchmark ZIP containing those predictions exists in the accessible project tree or the supplied policy ZIP. The policy ZIP explicitly contains no Gold or prediction data. No replacement model predictions were generated. The supplied historical validation report's V9 values are not treated as results for this integration because its postcode implementation was not verified against the repository's existing validator.

`evaluation/offline_regression.py` accepts only exact `--gold` and `--pred` paths, validates the expected row counts, applies the actual repository postal validator when `--include-existing-postal` is supplied, and reports Strict P/R/F1, TP/FP/FN, per-label counts, mask recall/unmasked Gold characters, policy changes, and postprocessing-only p50/p95. Saved 05 predictions do not carry detector status, so the script reports review rate as unavailable. It does not report model inference latency.

Because the frozen predictions are missing, changed-document counts, adverse true-positive removals on Official Test/Holdout, and combined Holdout 99% reproduction remain unverified. No document-specific exception was added.

## Step 2 handoff

The repository has no Step 2 pseudonymization implementation. The detector response and README now define the contract: downstream de-identification must use `mask_intervals`, route `REVIEW_REQUIRED` for review, and stop on `BLOCKED`. Tests verify the interval union preserves all pre-policy spans, but an end-to-end assertion against a real Step 2 caller could not be run.

## Existing postal validation is not duplicated

The new policy module has exactly two rules: `standalone_surname_title` and `explicit_internal_extension`. It has no address or postcode rule. `detector.py` still calls the original `validate_boundaries()` before `refine_spans()`, and regression tests continue to exercise its conditional postal expansion, non-postal field rejection, and overlap protection.
