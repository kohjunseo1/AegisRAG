"""Compare frozen V6 Step1 vs KLUE+No-Full on an external labeled JSONL.

Read-only. Actual pinned local NER inference (unlike A/B snapshot ablations).
The reference team repository, production pipeline, Gate, HMAC, and DB are NOT
modified. Never print document texts, gold values, tokens, or spans.

Example:
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python \
   -m unified_v1.external_validation --data ~/aegis_external_eval/team4/synth_hard_eval.jsonl --limit 5
 PYTHONPATH=step2_privacy_rag/app /tmp/aegis-unified-followup-model-env/bin/python \
   -m unified_v1.external_validation --data ~/aegis_external_eval/team4/synth_hard_eval.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from collections import Counter, defaultdict
from pathlib import Path

from .boost_probe import LABELS, fmt, predict, span_keys
from .cross_boost_probe import detector_v6
from .lean import Worker
from .lean_worker import reference_modules


def load_external(path):
    """Load through the repository's strict reader, independent of PYTHONPATH.

    File-invoked scripts add scripts/ rather than the repository root to
    sys.path; importing scripts.eval_blind_pii would fail in that context.
    Explicit local-file loading preserves the *same* read_rows validation
    without globally changing sys.path or reimplementing its contracts.
    """
    reader = Path(__file__).resolve().parents[3] / "scripts" / "eval_blind_pii.py"
    if not reader.is_file():
        raise RuntimeError("STRICT_EVAL_READER_NOT_FOUND")
    spec = importlib.util.spec_from_file_location("_aegis_strict_eval_reader", reader)
    if spec is None or spec.loader is None:
        raise RuntimeError("STRICT_EVAL_READER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.read_rows(Path(path))


def update(stats, predicted, gold):
    for label in ("ALL", *LABELS):
        p = predicted if label == "ALL" else {s for s in predicted if s[0] == label}
        g = gold if label == "ALL" else {s for s in gold if s[0] == label}
        out = stats[label]
        out["tp"] += len(p & g)
        out["fp"] += len(p - g)
        out["fn"] += len(g - p)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data", type=Path, required=True)
    cli.add_argument("--limit", type=int, default=None,
                     help="Positive first-N document smoke test; omit for full evaluation")
    cli.add_argument("--check-only", action="store_true",
                     help="Validate Gold schema/offsets; do not load or run any model")
    args = cli.parse_args()
    if args.limit is not None and args.limit <= 0:
        cli.error("--limit must be positive")

    path = args.data.expanduser().resolve()
    rows = load_external(path)
    original_count = len(rows)
    if args.limit is not None:
        rows = rows[:args.limit]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"EXTERNAL_INPUT_SHA256={digest} documents={len(rows)}/{original_count} "
          "source=external_jsonl", flush=True)
    print("WARNING: This is a public synthetic dataset; not a fresh blind mentor holdout.",
          flush=True)
    if args.check_only:
        print("EXTERNAL_SCHEMA_OFFSETS_PASS", flush=True)
        return 0

    # Same KLUE configuration and source code as the previous, parity-checked
    # C1 A/B probes; disable only the _full_names contribution in a pure replay.
    P, boost, H = reference_modules(__import__(
        "unified_v1.selection", fromlist=["REFERENCE"]
    ).REFERENCE)
    P.HYBRID = False
    P.ENSEMBLE = False
    P.BOOST_PERSON = P.BOOST_ADDRESS = True

    scores = {"V6_STEP1": defaultdict(Counter),
              "KLUE_NO_FULL": defaultdict(Counter)}
    gold_present = Counter()

    # Separate resident workers avoid loading 3 checkpoints in one process.
    # Worker implements the original frozen V6 and original team C1 paths.
    with Worker("B0") as v6, Worker("C1") as klue:
        for index, row in enumerate(rows, 1):
            text = row["text"]
            reference = span_keys(row["entities"])
            for label in LABELS:
                gold_present[label] += sum(s[0] == label for s in reference)

            v6_prediction = v6.call(text)
            v6_spans = span_keys(detector_v6(text, v6_prediction))

            klue_prediction = klue.call(text)
            klue_spans = predict("NO_FULL", text, klue_prediction["raw"],
                                  P, boost, H, boost._full_names)
            update(scores["V6_STEP1"], v6_spans, reference)
            update(scores["KLUE_NO_FULL"], klue_spans, reference)

            if index % 50 == 0 or index == len(rows):
                print(f"EXTERNAL_EVAL_PROGRESS {index}/{len(rows)}", flush=True)

    print("\nMODE             LABEL             TP    FP    FN      F1")
    for mode, labels in scores.items():
        for label in ("ALL", *LABELS):
            print(f"{mode:<16} {label:<19} {fmt(labels[label])}")
    print("EXTERNAL_EVAL_COMPLETE detector_only=true no_gate_hmac_or_db=true "
          "no_raw_text_export=true", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
