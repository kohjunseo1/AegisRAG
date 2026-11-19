# A.X V7: SAFE local data → preflight → training → paired evaluation → private Hub

STATUS: CODE PREPARED / NOT TRAINED / NOT UPLOADED / NOT DEPLOYED.

Source: PII-detect/pii-ax-mixed-v1 pinned revision c0f99b1dc113befefeff1a3091087d50bc3ea635 (existing 37-class **fine-tuned** ModernBERT checkpoint). Do **not** restart from a base ModernBERT. Frozen Veil V6 remains deployed baseline until full E2E safety comparison.

Destination on explicit authorized publish ONLY: PII-detect/pii-ax-single-v7, NEW PRIVATE model repository with best_model/ and training_manifest.json. Never overwrite mixed-v1 or the Veil repo. GitHub contains code, no model files, tokens, data or weights.

## 1. Data roles and previous history

- Prior notebook 03C_04A_finetuned_hybrid_AutoDrive.ipynb explicitly documents Official Dev already used in earlier model training, Generated Val 195 used to choose model/epoch, Official Test 200 reserved, Generated Locked and new Holdout kept closed.
- Old Official Dev may be reused as **replay**, not as new independent Dev, provided its data are locally accessible, licensed/authorized and annotations validated.
- Older Generated Val is exposed/development evidence; do not present it as fresh validation. Old Official Test / Locked / Holdout, team4 hard_eval and synth_fresh **MUST NOT** become V7 training or model-selection input.
- A previously obtained seven-label dataset is NOT guaranteed complete annotation for all 37 original model classes. Mark it seven_only. The scripts set loss target -100 outside labeled spans and use frozen-teacher KL to discourage erasing old outputs; this does not guarantee full preservation. Fully annotated old data may be marked full_37 after manual label-policy verification.
- Separate new, truly unseen Dev (for early stopping) and private holdout (final validation) from the training templates/names/address values. Starter synthetic Dev is only a **smoke test**, not convincing generalization validation.

## 2. Error evidence for targeted generation

Team4 public hard403 PERSON 979: exact 886, boundary 57, completely no-overlap 36. Of no-overlap 36, one/two Hangul character names account for 12+19=31. PERSON FP 121: same-class boundary 64 / unmatched 57.
ADDRESS 171: exact 152, boundary 16, no-overlap 3. Postal-code-in-Gold 11: 6 boundary. Lot style 87: 10 boundary.
These are *diagnostic* tags, not audited fine-grained surname/given-name categories. Before scaling data or setting label policy, locally inspect a small balanced sample of real failure categories to distinguish actual PII from a policy mismatch.

## 2-1. Small manual inspection of actual error contexts (LOCAL SENSITIVE)
The aggregate shape taxonomy alone is insufficient to decide whether a one/two-character PERSON is a genuine name, an honorific, or a data labeling mismatch. Inspect a balanced, small number of no-overlap/partial-boundary/unmatched FP examples **locally**, without copying those literal sentences to train:

    PYTHONPATH=step2_privacy_rag/app python scripts/ax_v7_review_cases.py \
        --data "$HOME/aegis_external_eval/team4/synth_hard_eval.jsonl" \
        --out step2_privacy_rag/local_eval/ax_v7/manual_case_review.jsonl \
        --confirm-local-sensitive-export

This file contains original PII snippets and is not suitable for GitHub, HF, ChatGPT or external LLMs. It is permission 0600, stored under gitignored local_eval. Audit labels and then write **new independent synthetic template families**; do not train on the error-window examples themselves.

## 3. Build bootstrap data and prepare old Dev replay

Run from repository root:

    git pull --ff-only origin feature/unified-v1
    python scripts/ax_v7_make_data.py --out step2_privacy_rag/local_eval/v7_bootstrap --replay-in /YOUR/LOCAL/official/dev.jsonl

This creates train.jsonl (targeted synthetic), dev.jsonl (new synthetic TEMPLATE SPLIT but not truly independent), replay.jsonl (old Official Dev conservatively marked seven_only). Never use official/test.jsonl in this command.

JSONL schema:
    {"id":"x01","text":"고객 성명: ...","entities":[{"label":"person","start":7,"end":10}],"annotation_scope":"seven_only"}

The bootstrap generator is a **data contract smoke fixture**, not a carefully curated final training corpus. Expand and manually review the positives/negative contrasts and label policies before final learning. It prints aggregate counts only.

## 4. Preflight (READ-ONLY, no training)

The existing project model runtime venv may not include GPU training dependencies. Use a new clean GPU-capable environment (Colab L4 recommended) after pinning and verifying compatible torch/transformers/accelerate versions.

    PY=/path/to/training/python
    DATA=step2_privacy_rag/local_eval/v7_bootstrap
    OUT=step2_privacy_rag/local_eval/ax_v7_run1
    "$PY" scripts/ax_v7_train.py preflight \
        --train "$DATA/train.jsonl" --dev "$DATA/dev.jsonl" \
        --replay "$DATA/replay.jsonl" \
        --hf-read-check

Checks model source at pinned HF revision or specified local --model-dir, 37-label id2label map and 7 target class aliases, 7-only vs full_37 annotation policy, Unicode spans, data duplication/split contamination, HF authentication READ access. A read check does not prove private repo WRITE permissions.

Stop if mapping is ambiguous or errors appear. NEVER guess a missing label alias or force unlabeled legacy classes to O.

## 5. Training (local only)

    "$PY" scripts/ax_v7_train.py train \
        --train "$DATA/train.jsonl" --dev "$DATA/dev.jsonl" \
        --replay "$DATA/replay.jsonl" \
        --out "$OUT" --epochs 2 --lr 0.00001 --batch-size 2 \
        --grad-accum 8 --distill-weight 0.5

The original 37-way classifier is continued without reinitialization; teacher KL retains prior outputs, but old classes may still regress. Validate locally on fresh reviewed new Dev. The command never pushes to Hub and fails if output folder already has contents. On CPU, training is impractically slow: run on a GPU environment when possible.

## 6. Same-policy, local-only evaluation (before approval)

    PYTHONPATH=step2_privacy_rag/app "$PY" scripts/ax_v7_eval.py \
        --checkpoint "$OUT/best_model" \
        --data "$HOME/aegis_external_eval/team4/synth_hard_eval.jsonl" --compare-v6

This prints per-type strict TP/FP/FN/F1, actual Step1 mask character coverage, fully/partly unprotected Gold, and pipeline timing. Treat hard403 as an EXPOSED regression set, not fresh generalization proof. B171 is also exposed.

Before any deployment or upload, compare V6 vs V7 on a genuinely unseen, isolated final holdout, then run actual document/query Gate, HMAC integrity, false READY, retrieval Hit@1/5, MRR, Confuse@k, and output recheck tests. There is no reliable zero-FN guarantee on arbitrary unknown documents.

## 7. Optional PRIVATE Hugging Face publish (explicit action ONLY)

After manual approval and only with a HF fine-grained token containing appropriate PII-detect organization write permission:

    "$PY" scripts/ax_v7_train.py upload \
        --out "$OUT" \
        --confirm-upload-to PII-detect/pii-ax-single-v7

- This explicitly CREATES NEW PRIVATE PII-detect/pii-ax-single-v7 and uploads best_model/ plus model provenance. It refuses an existing target and does not rewrite the frozen source model.
- The required repository write permission cannot be proven without a write call. Only test it once the new version is explicitly approved; use hf auth login / HF_TOKEN in your machine, never copy keys into the chat.
- A successful private upload is NOT deployment approval. The Step1 model manifest must remain pinned to V6 until Gate/HMAC and E2E regressions pass.

## 8. Output recheck, separate from initial PII detection

Existing generation.PrivacyRecheck verifies authorized tokens, structural residual patterns, and requires an independent engine. It fails closed if engine unavailable. A.X used again on output is not independent confirmation of an unknown missed name; it can repeat the same mistake. Do not send unmasked original PII to an external LLM to check. Decide conditional independent local verifier versus comprehensive structural+token validations after leak-seeding tests and latency profiling.

Read these tracked sources:
- scripts/ax_v7_train.py
- scripts/ax_v7_make_data.py
- scripts/ax_v7_eval.py
- tests/test_ax_v7_train_prep.py
- reports/unified_v1/V7_SINGLE_AX_TRAINING_PLAN.md
