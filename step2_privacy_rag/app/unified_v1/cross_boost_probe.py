"""Cross-apply teammate's name boost to frozen V6 Step1 detections.

Read-only offline evaluation. Uses cached C1, AX and Veil outputs.
Does not run models, approve documents, modify the V6 pipeline or access DB/API.

Run from the AegisRAG-unified-v1 repository:
 PYTHONPATH=step2_privacy_rag/app python -m unified_v1.cross_boost_probe
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

from . import selection as S
from .boost_probe import fmt, predict, span_keys
from .experiments import load_rows
from .lean import read
from .lean_worker import reference_modules


def overlap(a, b):
    """True for intersecting half-open (start, end) spans; touching is not overlap."""
    return a[0] < b[1] and b[0] < a[1]


def detector_v6(text, snapshot):
    """Reconstruct frozen detector-only Step1, NOT Gate/HMAC typed entities."""
    sys.path.insert(0, str(S.ROOT / "step1_pii_detector" / "src"))
    from pii_detector.detector import fuse_labelwise, validate_boundaries
    from pii_detector.structural_policy import refine_spans

    sources = snapshot["by_source"]
    ax, veil = sources["ax"], sources["veil"]
    combined, _ = fuse_labelwise(ax, veil)
    bounded, _ = validate_boundaries(text, combined)
    final, _ = refine_spans(text, bounded)
    return final


def add_stats(stats, pred, gold):
    for label in ("ALL", "person", "address", "account_number"):
        p = pred if label == "ALL" else {s for s in pred if s[0] == label}
        g = gold if label == "ALL" else {s for s in gold if s[0] == label}
        c = stats[label]
        c["tp"] += len(p & g)
        c["fp"] += len(p - g)
        c["fn"] += len(g - p)


def main():
    # Fail immediately if pairwise span-overlap logic is accidentally changed.
    assert overlap((1, 3), (2, 4))
    assert overlap((2, 4), (1, 3))
    assert not overlap((1, 3), (3, 5))
    assert not overlap((3, 5), (1, 3))
    assert not overlap((1, 2), (3, 4))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", choices=("A", "B"),
                        default=["A", "B"])
    args = parser.parse_args()

    P, boost, H = reference_modules(S.REFERENCE)
    P.HYBRID = False
    P.ENSEMBLE = False
    P.BOOST_PERSON = P.BOOST_ADDRESS = True

    for ds in args.datasets:
        rows = load_rows(S.A if ds == "A" else S.B)
        cached_k = read(f"C_live_{ds}_C1.json")
        cached_v = read(f"C_live_{ds}_B0.json")
        ids = {row["id"] for row in rows}
        if set(cached_k) != ids or set(cached_v) != ids:
            raise RuntimeError(f"{ds}: snapshot IDs differ from corpus")

        modes = ("V6_STEP1", "V6_PLUS_BOOST", "V6_PLUS_BOOST_FILTER",
                 "KLUE_C1", "KLUE_NO_FULL")
        scores = {mode: defaultdict(Counter) for mode in modes}
        effects = Counter()
        parity_errors = 0

        for row in rows:
            doc_id, text = row["id"], row["text"]
            gold = span_keys(row["entities"])

            v6_spans = detector_v6(text, cached_v[doc_id])
            base = span_keys(v6_spans)

            # The only proposed edit to team boost is excluding its broad
            # _full_names generator. The reference code remains unchanged.
            with patch.object(boost, "_full_names", return_value=[]):
                boosted = boost.boost_persons(text, v6_spans, kiwi=None)

            # Preserve every original V6 typed entity (including its bounds).
            # Add only non-overlapping PERSON candidates; no Gold-dependent
            # selection, no replacement of other PII labels.
            extra = {
                ("person", s["start"], s["end"])
                for s in boosted if s["label"] == "person"
                and not any(overlap((s["start"], s["end"]),
                                    (b[1], b[2])) for b in base)
            }
            filtered, _ = H.post_filter(
                text, [dict(label=l, start=a, end=b) for l, a, b in sorted(extra)]
            )
            accepted = span_keys(filtered)

            # Run original KLUE C1 and its already-proposed no-full variant
            # using its own pinned cached Raw outputs, without model forward.
            raw_k = cached_k[doc_id]["raw"]
            c1 = span_keys(cached_k[doc_id]["spans"])
            no_full = predict("NO_FULL", text, raw_k, P, boost, H,
                              boost._full_names)
            # Sampled C1 original replay verifies reference-code parity.
            if ds == "A" and len(scores["KLUE_C1"]["ALL"]) == 0:
                replay = predict("ORIGINAL", text, raw_k, P, boost, H,
                                 boost._full_names)
                if replay != c1:
                    parity_errors += 1

            outputs = {
                "V6_STEP1": base,
                "V6_PLUS_BOOST": base | extra,
                "V6_PLUS_BOOST_FILTER": base | accepted,
                "KLUE_C1": c1,
                "KLUE_NO_FULL": no_full,
            }
            for mode, pred in outputs.items():
                add_stats(scores[mode], pred, gold)

            effects["boost_extra_tp"] += len(extra & gold)
            effects["boost_extra_fp"] += len(extra - gold)
            effects["filter_kept_tp"] += len(accepted & gold)
            effects["filter_kept_fp"] += len(accepted - gold)
            effects["v6_gold_lost_by_augmentation"] += len(
                (base & gold) - ((base | accepted) & gold)
            )

        if parity_errors:
            raise RuntimeError(f"{ds}: C1 original replay failed parity check")

        # Regression check: these are the frozen Step1 detector-stage scores.
        expected = {"A": (910, 10, 2), "B": (1430, 61, 9)}[ds]
        base_score = scores["V6_STEP1"]["ALL"]
        observed = tuple(base_score[x] for x in ("tp", "fp", "fn"))
        if observed != expected:
            raise RuntimeError(
                f"{ds}: V6 detector reconstruction mismatch; "
                f"expected {expected}, observed {observed}"
            )

        print(f"\n=== {ds}: CROSS BOOST (EXPOSED / DIAGNOSTIC ONLY) ===")
        print("MODE                   ALL: TP FP FN F1          PERSON: TP FP FN F1")
        for mode in modes:
            print(f"{mode:<23}{fmt(scores[mode]['ALL'])}     "
                  f"{fmt(scores[mode]['person'])}")
        print("V6 PERSON ADDITIONS:", " ".join(
            f"{name}={value}" for name, value in sorted(effects.items())
        ))
        print("All candidate additions keep V6 Step1 spans unchanged.")
        print("These metrics are detector-only; no Gate/HMAC safety "
              "or indexability is demonstrated.")


if __name__ == "__main__":
    main()
