# V7 single AX experiment plan — frozen baseline and safety gates (2026-11-20)

STATUS: PLANNED, NOT TRAINED. Current V6 A.X+Veil checkpoint and Gate contracts remain unchanged.

## 1. Confirmed observations
- B PERSON 854: exact 849, boundary 4, no-overlap 1. 4 boundary errors have following josa. B ADDRESS 123: V6 exact 123; AX-only same-policy exact 120, boundary 3.
- Team4 public hard403 PERSON 979: exact 886, boundary 57, no-overlap 36. One-character 39: exact 23, boundary 4, no-overlap 12. Two-character 430: exact 388, boundary 23, no-overlap 19. Thus 31 of 36 total no-overlap PERSON have length 1–2, but length is NOT an annotation of surname/given-name role. Following josa 341: exact 310, boundary 24, no-overlap 7. Spaced names 239: exact 219, boundary 16, no-overlap 4. All feature tags can overlap.
- Hard403 PERSON FP 121: 64 boundary-overlap, 57 unmatched. ADDRESS Gold 171: exact 152, boundary 16, no-overlap 3. Postal code included in 11: exact 5, boundary 6. Lot-style 87: exact 77, boundary 10. Address FP 24: 15 boundary-overlap, 9 unmatched.
- V6 vs same-V6-policy AX-only: B strict F1 .976109 vs .972402; hard403 .920555 vs .917274. Differences under this common policy are ADDRESS only; PERSON unchanged.
- Baseline hard403 V6 mask character recall .987756, 39 completely uncovered and 79 partly uncovered Gold **across all labels**, not just person/address. Not approved-output leakage measurements.
- B and hard403 were inspected during development; scores are regression diagnostics only, not independent blind generalization proof.

## 2. CRITICAL prerequisite: retain the 37-class AX head
Source: step1_pii_detector/src/pii_detector/backends.py. The frozen checkpoint PII-detect/pii-ax-mixed-v1 at c0f99b1dc113befefeff1a3091087d50bc3ea635 has num_labels=37. Step1 emits only seven mapped PII types; it does not have just 15 BIO classes.

Continue training original AX classifier with identical tokenizer, 37 class IDs, BIO scheme, decoder and offset unit. Creating a fresh seven-type 15-class head changes/reinitializes the classifier and invalidates simple "continued training" comparisons.

Verify original label mappings and whether labels outside seven emitted types occur in new data. In partially labeled 7-type corpora, do NOT assume every unannotated older PII class is O. Preserve non-target label knowledge with compatible old data, ignored unknown labels, or cautious old-checkpoint distillation as appropriate. Never start training before class compatibility is checked.

The public original AX training source id4thomas/ko-pii-detector has a token-classification Trainer and noise injection logs. It records explicit data augmentation label-policy conflicts, so inspect before reuse. No model training has been launched.

## 3. Training data specification
PERSON:
- Positive: truly single-character names; 2-character surname+given vs 2-character given-only; spaces within names; surname-omitted mentions; contextual job titles; suffix particles and name-final "이"; long/non-Hangul names.
- **Strong negative**: ordinary short nouns, document field titles, business/legal terms, organization names, role-only mentions, surname + job title under the chosen annotation policy, ambiguous common-word-as-name usages.
- Label single-character/surname-only cases by a documented policy, not by string length; retain whole document context.

ADDRESS:
- Positive: street/lot style, abbreviated provinces, missing/reordered spaces, internal unit/building, parenthesized/trailing postal code, line breaks, partial address **only when the data policy labels it PII**.
- Strong negative: region/city alone, generic locations, numbers in order/case/account fields, postal-shaped unrelated numbers.
- Freeze consistent labeling rule for whether ZIP belongs inside Gold. Never blindly extend into numeric fields.

Keep all seven existing classes in training replay; include enough original-domain labeled examples to test catastrophic forgetting. Use synthetic/approved data only. Construct many independent templates and values; train/dev/test partition by template family, person names, address components, and normalized text fingerprints to avoid contamination. Regenerate original Unicode [start,end) spans after every textual augmentation; validate conflicts and token-label alignment.

DO NOT train on literal A/B/hard403 documents or repeatedly tune against exposed scores. Reserve synth_fresh as one post-selection distribution check and acquire an actually unseen separately generated/annotated test set for strong generalization evidence.

## 4. Bounded candidate test, not endless hyperparameter search
P0: immutable V6 and original AX-alone same-policy baseline.
P1: data/schema/tokenizer/BIO quality gate including offset round trip and 37-label map check.
P2: one conservative fine-tune from mixed-v1 checkpoint (first trial: low LR, 1–3 epochs, early stop on new dev; optionally two seeds); preserve head and log hashes.
P3: compare frozen AX vs AX fine-tuned under IDENTICAL V6-like policy (without Veil), plus original V6. Report per-label TP/FP/FN, strict/masking recall, fully/partly uncovered Gold, non-target label regression and OOD families. Keep model and rule gains separate.
P4: optional SMALL broad rule-family ablations for residual structural errors only: evidence-qualified josa/name boundary, address/postal field and overlap guards, within-document verified repeats. No known-name dictionary or dataset-specific phrase list. Finalize max one well-supported correction per general failure class, each tested with hard negatives.
P5: same-contract CPU p50/p95, RSS/cold start/window and Gate/HMAC/false READY evaluation and same 88-query retrieval+Confuse. The previous AX-C2 and V6 timings include different processing stages: do NOT infer exact future speedup from them. Fine-tuning does not itself speed up the AX encoder.

Adoption requires no deterioration in actual approved-document PII protection or HMAC label/offset validity, and strong accuracy/generalization at materially lower actual CPU/RSS. Otherwise keep V6 and advance E2E.

## 5. Normalization and later output recheck
- NER must preserve raw-text Unicode offsets. A normalized alternative view (NFKC, spacing, separator, province aliases) requires a reversible source-offset alignment; do not overwrite source before labeling/masking.
- Keep raw detection spans, typed HMAC-approved spans, unresolved mask/review intervals, and post-approval query/retrieval canonicalization as separate interfaces.
- Existing Step2 PrivacyRecheck in step2_privacy_rag/app/unified_v1/generation.py requires independent_engine and fails if it is absent. Changing ingestion to single AX **does not** permit silently turning off output recheck. First assess authorized-token whitelist, shape/structural residual guards and local conditional independent detector against seeded leak tests. Running the exact same AX twice is not independent confirmation.
- Never send raw/unapproved PII to HCX-007 or external LLM for privacy validation. Live generation accepts only approved pseudonymized context. Fail closed and review unresolved privacy.

## Stop rule
One focused AX fine-tune and controlled rule test, followed by a genuinely unseen holdout and full E2E. If no material safe Pareto gain, keep frozen V6; avoid endlessly optimizing public/development datasets.

References:
- https://github.com/id4thomas/ko-pii-detector/blob/main/docs/training_log.md
- https://github.com/id4thomas/ko-pii-detector/blob/main/src/train_token_clf.py
- https://huggingface.co/docs/transformers/tasks/token_classification
