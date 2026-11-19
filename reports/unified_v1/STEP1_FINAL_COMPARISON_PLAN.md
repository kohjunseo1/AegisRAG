# Step1 final comparison — pinned test matrix (2026-11-20)

## Verified evidence (not a fresh blind evaluation)

| Data | V6 strict F1 | 5-team KLUE C1 F1 | KLUE No-Full F1 |
|---|---:|---:|---:|
| A mentor holdout 187 / 912 | 0.993450 | 0.995063 | 0.993407 |
| B previously exposed blind 171 / 1439 | 0.976109 | 0.952284 | 0.961749 |
| Team4 hard eval 403 / 1958 | 0.920555 | unmeasured | 0.795130 |

- Team4 public v10 rules+atonlee on hard_eval: strict F1 **0.8655**, mask character recall **0.9641**. Source: 실전프로젝트1/results/final_synth_hard_eval/summary.csv.
- Team4 final-project README documents later complete_spans and extra fine-tuned atonlee-ft-v6. synth_fresh masking recall **0.9790 → 0.9858**, name recall **0.9571 → 0.9743**. Another synth_hard_eval v4 training comparison records **0.929 → 0.964** masking recall. **New final-project strict F1 is not published in that README.** Updated model weights are **not in GitHub**. Never label v10 F1 as final-project F1.
- 5-team feature/detect-merge/starter/detect_merge.py comments report B 171-doc **0.992 exact-Gold rate and 0 completely uncovered Gold mentions**, **not strict F1**. Pinned B1 uses a no-ensemble configuration; it is not necessarily identical to the teammate's runtime environment.
- Prior V6+name Boost is rejected: B TP +1 / FP +49; A no change. AX+KLUE non-overlap union also creates many FP.

## Final detector experiment

Script: step2_privacy_rag/app/unified_v1/merge_validation.py

Reproducibility:
- Team detector reference commit: 242543bf0eae91aa3d655227a747aa128707368e (8bitHermitcrab/privacy-rag-chatbot feature/detect).
- Team merge implementation reproduced from feature/detect-merge/starter/detect_merge.py blob 52216a7911bd361df11ff82a8406a02460740d2e.
- Local pinned weights: roberta_small_v1, koelectra_ft5, frozen V6 A.X+Veil; all inference offline and no paid calls.
- Original teammate V6 client passes **mask_intervals** converted to typed spans (fallback label person), not V6 Step1 typed entities.
- Original team _drop_overlaps is applied to merged spans.

Modes, all scored with same Gold and strict scorer:

1. V6_TYPED: frozen Step1 typed entities, real Step1 masking intervals scored for coverage.
2. V6_MASK_VIEW: V6 mask intervals converted to typed spans by teammate's v6_client.
3. HYBRID_B1: pinned KLUE+KoELECTRA hybrid; P.ENSEMBLE=False in this benchmark.
4. TEAM_MERGE: teammate original merge of B1 and V6_MASK_VIEW, followed by original overlap removal.
5. MERGE_TYPED_DIAG: control using V6 typed entities instead (not teammate production design).
6. Optional TEAM4_V10: public precomputed team4 v10 predictions (not their newer trained model).

Reported: strict TP/FP/FN/F1 across 7 labels; mask character recall and fully/partly uncovered Gold; disagreement review estimates (NOT actual Gate); CPU worker inference and merge-only timing with per-worker RSS. V6 worker timing includes frozen prepare, thus is NOT like-for-like pure model inference with team Hybrid.

Commands from repo root:

~~~bash
git pull --ff-only origin feature/unified-v1
PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.merge_validation --dataset B --check-only
PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.merge_validation --dataset B --limit 5
PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.merge_validation --dataset B
PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python -m unified_v1.merge_validation --data "$HOME/aegis_external_eval/team4/synth_hard_eval.jsonl"
~~~

Team4 public v10 predictions for optional --team4-preds on 403:
https://raw.githubusercontent.com/BBuSang/likelion-AI-4team/main/실전프로젝트1/preds/combined/rules+atonlee_synth_hard_eval.jsonl

## Selection and stop rule

- A and B were inspected; B is NOT independent blind evidence anymore.
- Fix all candidate settings before checking hard_eval. Do not tune on hard_eval. Reserve synth_fresh for one post-selection distribution check.
- A merged candidate must not weaken masking and must satisfy an actual Gate/HMAC approval contract, not simply have higher overlap recall.
- Reject a four-model merge if benefits cannot justify added FP, review work, CPU and RAM.
- If unconvincing, freeze V6 as baseline and move to document/query Gate, HMAC, DB, and E2E rather than endlessly adding rules.
- Confuse@k, Hit@k, MRR and query coverage need the identical indexed corpus and same 88-query suite. They are NOT derived from Stage1 scores.
- Team merge "review" is a disagreement proposal; it is not yet the application's document/query Gate decision. Fail closed on model errors or unknown input.
