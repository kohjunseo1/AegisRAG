"""Read-only C1 full-name boost ablation using pinned local inference snapshots.

No model forward, HMAC key, database, external API, or source-repo writes.
A and B are exposed diagnostic datasets, NOT fresh generalization holdouts.

Run:
 PYTHONPATH=step2_privacy_rag/app python -m unified_v1.boost_probe
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import nullcontext
from unittest.mock import patch

from . import selection as S
from .experiments import load_rows
from .lean import read
from .lean_worker import reference_modules

MODES = ("ORIGINAL", "NO_FULL", "STRICT_FULL")
LABELS = ("person", "address", "account_number", "card_number",
          "phone_number", "email", "resident_registration_number")


def span_keys(rows):
    return {(x["label"], x["start"], x["end"]) for x in rows}


def strong_full_evidence(text, candidate, boost):
    """Require direct person-field/role/title evidence, not an organization or
    another PII in the same table row. Reuse the teammate's language patterns;
    do not use Gold values, name allowlists, document IDs, or probabilities.
    """
    start, end = candidate[:2]
    left = boost._line_left(text, start)
    return bool(
        boost.LEFT_LABEL.search(left)
        or boost.ROLE_LEFT.search(left)
        or boost.TITLE_SLASH.search(left)
        or boost.right_strong(text, end)
    )


def predict(mode, text, raw, pipeline, boost, hybrid, original_full):
    """Replay the exact original pipeline; change only _full_names in isolation."""
    pipeline.model_predict = lambda _text: [dict(x) for x in raw]
    if mode == "ORIGINAL":
        scope = nullcontext()
    elif mode == "NO_FULL":
        scope = patch.object(boost, "_full_names", return_value=[])
    elif mode == "STRICT_FULL":
        def filtered(t, other):
            return [c for c in original_full(t, other)
                    if strong_full_evidence(t, c, boost)]
        scope = patch.object(boost, "_full_names", side_effect=filtered)
    else:
        raise ValueError(f"unknown mode: {mode}")
    with scope:
        stages = pipeline.detect(text)
    kept, _dropped = hybrid.post_filter(text, stages)
    return span_keys(kept)


def add_score(stats, prediction, truth):
    by_label = set(LABELS)
    for label in by_label | {"ALL"}:
        p = prediction if label == "ALL" else {x for x in prediction if x[0] == label}
        g = truth if label == "ALL" else {x for x in truth if x[0] == label}
        c = stats[label]
        c["tp"] += len(p & g)
        c["fp"] += len(p - g)
        c["fn"] += len(g - p)


def f1(c):
    denom = 2 * c["tp"] + c["fp"] + c["fn"]
    return 2 * c["tp"] / denom if denom else 1.0


def fmt(c):
    return f"{c['tp']:>5} {c['fp']:>5} {c['fn']:>5}  {f1(c):.6f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", choices=("A", "B"),
                        default=["A", "B"])
    args = parser.parse_args()

    pipeline, boost, hybrid = reference_modules(S.REFERENCE)
    original_full = boost._full_names
    # Explicitly disable the second NER and probability ensemble as in C1.
    pipeline.HYBRID = False
    pipeline.ENSEMBLE = False
    pipeline.BOOST_PERSON = True
    pipeline.BOOST_ADDRESS = True

    for dataset in args.datasets:
        source = S.A if dataset == "A" else S.B
        rows = load_rows(source)
        cached = read(f"C_live_{dataset}_C1.json")
        if set(cached) != {r["id"] for r in rows}:
            raise RuntimeError(f"{dataset}: snapshot/document IDs mismatch")

        scores = {m: defaultdict(Counter) for m in MODES}
        changes = {m: defaultdict(Counter) for m in MODES[1:]}
        parity_mismatches = 0

        for row in rows:
            key = row["id"]
            gold = span_keys(row["entities"])
            snapshots = cached[key]
            results = {}
            for mode in MODES:
                results[mode] = predict(
                    mode, row["text"], snapshots["raw"],
                    pipeline, boost, hybrid, original_full,
                )
                add_score(scores[mode], results[mode], gold)

            if results["ORIGINAL"] != span_keys(snapshots["spans"]):
                parity_mismatches += 1

            baseline = results["ORIGINAL"]
            for mode in MODES[1:]:
                candidate = results[mode]
                for label in ("ALL", "person"):
                    old = baseline if label == "ALL" else {
                        x for x in baseline if x[0] == "person"
                    }
                    new = candidate if label == "ALL" else {
                        x for x in candidate if x[0] == "person"
                    }
                    g = gold if label == "ALL" else {
                        x for x in gold if x[0] == "person"
                    }
                    d = changes[mode][label]
                    d["recovered_tp"] += len((new - old) & g)
                    d["lost_tp"] += len((old - new) & g)
                    d["removed_fp"] += len((old - new) - g)
                    d["added_fp"] += len((new - old) - g)

        if parity_mismatches:
            raise RuntimeError(
                f"{dataset}: original C1 cache parity FAILED "
                f"({parity_mismatches} documents); results not comparable"
            )

        print(f"\n=== {dataset}: C1 full-name evidence probe "
              f"({len(rows)} docs; EXPOSED diagnostic set) ===")
        print("MODE                ALL TP FP FN F1                   PERSON TP FP FN F1")
        for mode in MODES:
            print(f"{mode:<19} {fmt(scores[mode]['ALL'])}       "
                  f"{fmt(scores[mode]['person'])}")

        print("STRICT_FULL vs ORIGINAL, PERSON: "
              + " ".join(f"{k}={v}" for k, v in
                         sorted(changes["STRICT_FULL"]["person"].items())))
        print("NO_FULL vs ORIGINAL, PERSON: "
              + " ".join(f"{k}={v}" for k, v in
                         sorted(changes["NO_FULL"]["person"].items())))

    print("\nAll scores are strict span diagnostics; no model was selected "
          "and no Gate/HMAC/E2E evaluation was performed.")


if __name__ == "__main__":
    main()
