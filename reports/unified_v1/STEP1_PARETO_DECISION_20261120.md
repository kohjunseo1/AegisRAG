# Step1 candidate comparison / AX–Veil ablation decision (2026-11-20)

All results below were measured on exposed or public synthetic data. **A/B/hard_eval are no longer independent blind sets.** The original detector **Step1 strict metrics** must not be confused with post-Gate HMAC output metrics or original-text leakage.

## Detector-only Strict TP / FP / FN / micro-F1

| Candidate | A 187 / 912 | B 171 / 1439 | Team4 hard 403 / 1958 |
|---|---|---|---|
| V6 frozen AX+Veil | 910 / 10 / 2 / **0.993450** | 1430 / 61 / 9 / **0.976109** | 1825 / 182 / 133 / **0.920555** |
| B1 pinned KLUE+KoELECTRA | 908 / 12 / 4 / 0.991266 | 1412 / 118 / 27 / 0.951162 | 1592 / 400 / 366 / 0.806076 |
| C1 KLUE+Boost | 907 / 4 / 5 / 0.995063 | 1407 / 109 / 32 / 0.952284 | not measured in same form |
| KLUE No-Full | 904 / 4 / 8 / 0.993407 | 1408 / 81 / 31 / 0.961749 | 1502 / 318 / 456 / 0.795130 |
| C2 AX-only + corrections | 910 / 20 / 2 / 0.988056 | **not yet measured** | **not yet measured** |
| 5-team original V6+Hybrid merge | not measured | 1428 / 191 / 11 / 0.933944 | 1748 / 316 / 210 / 0.869219 |
| Team4 v10 public rules+atonlee | 911 / 8 / 1 / 0.995085 (predictions parity, no same-scope timing) | not measured | 1676 / 239 / 282 / 0.865479 |

B masking character recall / completely uncovered / partly uncovered:
- V6: 0.999518 / 1 / 3.
- Team B1: 0.998716 / 4 / 8.
- Team original Merge: 0.999839 / 0 / 2.

Hard403 masking character recall / completely uncovered / partly uncovered:
- V6: 0.987756 / 39 / 79.
- Team B1: 0.943496 / 104 / 220.
- Team original Merge: 0.990141 / 25 / 66.
- Team4 public v10: 0.964062 / 161 / 197.

**Merge's masking gain is real but its typed F1 and number label quality degrade substantially.** Keep masking intervals distinct from typed entity labels; unconfirmed spans must not be automatically HMAC-approved.

## Runtime and routing facts

Frozen V6 implementation has fixed ownership: AX is the owner for 6 labels, **Veil is the ADDRESS owner** (see step1_pii_detector/src/pii_detector/detector.py::FROZEN_OWNER).

Existing 30-request controlled warm per-source benchmark:
- AX p50/p95 51.15/60.51 ms, Veil 14.13/15.44 ms, KLUE 16.42/23.05 ms, KoELECTRA 10.57/12.51 ms. These are controlled short inputs, not end-to-end corpus metrics.
- A C2 AX-only + corrections p95 398.52 ms, RSS peak 792 MiB, versus A V6 common-adapter p95 1164.52 ms, RSS peak 2209 MiB; **different output processing scopes**, not proof that Veil itself costs 766 ms.
- B V6 worker includes frozen prepare: mean/p95 2231.88/4202.53 ms; B team B1 detector mean/p95 389.97/615.54 ms; **do not compare the two as pure model forward**.
- Hard403 V6 including prepare mean/p95 329.45/510.83 ms, team B1 detector 107.53/160.08 ms. Text-length distribution affects the workload.
- A median text length 199 chars, B median 644 chars and max 1570 chars: window count / protection policy will matter.
- Veil is *not* the dominant isolated model-forward cost; AX is slower. AX-only may still reduce loading/RSS and processing path overhead, but the address-specific precision/recall loss must be tested.

## 4-team later model vs v10

Team4 v10 hard403: strict-F1 **0.865479**, mask char recall **0.964062**. Team4 later final project README documents full-pipeline holdout (already seen) F1 **0.995**, mask recall **0.999**, and later name-boost model:
- synth_hard_eval later reported mask recall **0.929 → 0.964** (earlier v4 training comparison), **no strict F1 on the new final model published there**.
- synth_fresh 1775 later reported mask recall **0.9790 → 0.9858** (fine-tuned v6 name boost), **no strict F1 for this new model published there**.
- Fine-tuned weights reported as external team-drive files; public repository does not provide the exact trained checkpoint. Do not claim V6 decisively exceeds Team4's *latest* model, only that V6 exceeds published v10 on hard403 strict-F1.

## One decisive next test: Veil ablation

Run script: step2_privacy_rag/app/unified_v1/ax_veil_ablation.py.
Compare on B and hard403, same V6 policy and Gold:
- V6_FROZEN: original source ownership (AX six types, Veil ADDRESS).
- AX_ALL_V6_POLICY: counterfactual same detector fusion/validation/structural policy with AX ADDRESS replacing Veil ADDRESS.
- AX_C2_CORRECTIONS: tested existing alternate AX-only heuristic policy.
- AX_RAW: reference raw AX spans.

Print all seven label strict F1, mask character coverage, fully/partly uncovered Gold, warm CPU timing and RSS. V6 worker timing includes frozen prepare; AX C2 timing includes its corrections, not the same output stage. Check AX raw output parity across backends; **do not treat the counterfactual as a production fail-open fallback**.

Decision:
1. If AX-only under an appropriate address policy is **non-inferior on externally confirmed masking and label accuracy**, materially faster / smaller, then AX-only can be promoted to a *new* deployable candidate with validated Gate/HMAC invariants. Do not silently remove required Veil from the current V6.
2. If AX-only loses address coverage/label integrity, retain V6 and investigate same-policy end-to-end latency (window counts, AX forward, Veil forward, Gate/prepare).
3. **Fine-tune AX only if a repeatable, broadly applicable gap remains**, with new train/dev split, pinned checkpoint and untouched test. Do not tune from already exposed A/B/hard to raise their score.
4. Freeze Step1 after this ablation and one final same-contract check. RAG Hit@k, MRR, Confuse@k, identity-resolution and query/document Gate require actual E2E measurement; no claims from Step1.
