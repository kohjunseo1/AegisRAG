#!/usr/bin/env python3
"""Resume an offline Hard403 V7 mask audit without changing Gold or model.

Only --review prints local synthetic source snippets; the decision JSON contains
no source text. Existing audit uses total_chars, not gold_chars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

EXPECTED_SHA = "2497c882307e604a9359bf463c485138fe29c0ea62e9e2360338dd23c006e4ab"
DECISIONS = {"1": "VALID_PII", "2": "INVALID_GOLD", "3": "POLICY_DISPUTED", "4": "INSUFFICIENT_CONTEXT"}
CAUSES = {"1": "MODEL_NO_OVERLAP", "2": "BOUNDARY", "3": "LABEL_CONFLICT", "4": "MASK_GAP", "5": "GOLD_POLICY", "6": "UNKNOWN"}


def cid(c):
    return f"{c['document_index']}:{c['gold_index']}"


def validate(data, audit):
    if not data.is_file() or not audit.is_file():
        raise ValueError("DATA_OR_AUDIT_FILE_NOT_FOUND")
    source = data.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    record = json.loads(audit.read_text(encoding="utf-8"))
    if digest != EXPECTED_SHA or record.get("dataset_sha256") != digest:
        raise ValueError("HARD403_SHA_MISMATCH")
    docs = [json.loads(line) for line in source.decode("utf-8-sig").splitlines() if line.strip()]
    cases = record.get("cases")
    if len(docs) != 403 or record.get("documents") != 403 or not isinstance(cases, list):
        raise ValueError("EXPECTED_HARD403_AUDIT")
    if Counter(c.get("coverage") for c in cases) != {"FULL": 19, "PARTIAL": 27}:
        raise ValueError("EXPECTED_FULL19_PARTIAL27")
    seen = set()
    for c in cases:
        key = cid(c)
        if key in seen:
            raise ValueError("DUPLICATE_AUDIT_CASE")
        seen.add(key)
        di, gi = c["document_index"], c["gold_index"]
        if not (type(di) is int and type(gi) is int and 0 <= di < 403 and
                0 <= gi < len(docs[di]["entities"])):
            raise ValueError("BAD_CASE_INDEX")
        gold = docs[di]["entities"][gi]
        counts = [c[k] for k in ("total_chars", "gold_chars") if k in c]
        if not counts or any(type(x) is not int for x in counts) or len(set(counts)) != 1:
            raise ValueError("INVALID_AUDIT_GOLD_LENGTH")
        total = counts[0]
        protected = c.get("covered_chars")
        if (total < 1 or gold["label"] != c["label"] or
                gold["end"] - gold["start"] != total or
                type(protected) is not int or not 0 <= protected < total or
                (c["coverage"] == "FULL") != (protected == 0)):
            raise ValueError("AUDIT_GOLD_OR_COVERAGE_MISMATCH")
    return digest, docs, cases


def load_decisions(path, digest, cases):
    if not path.exists():
        return {"dataset_sha256": digest, "scope": "manual_audit_not_gold_correction", "decisions": {}}
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("dataset_sha256") != digest or not isinstance(saved.get("decisions"), dict):
        raise ValueError("DECISIONS_SHA_OR_SCHEMA_MISMATCH")
    if set(saved["decisions"]) - {cid(c) for c in cases}:
        raise ValueError("DECISION_CASE_NOT_IN_AUDIT")
    return saved


def save_decisions(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    if os.name == "posix":
        path.chmod(0o600)


def print_summary(cases, saved):
    decisions = saved["decisions"]
    print("TOTAL_CASES:", len(cases))
    print("REVIEWED:", sum(cid(c) in decisions for c in cases))
    print("PENDING:", sum(cid(c) not in decisions for c in cases))
    print("MASK_BREAKDOWN:")
    for (coverage, label), count in sorted(Counter((c["coverage"], c["label"]) for c in cases).items()):
        print(" ", coverage, label, count)
    print("ADJUDICATION_BREAKDOWN:")
    for (coverage, decision), count in sorted(Counter((c["coverage"], decisions.get(cid(c), {}).get("decision", "PENDING")) for c in cases).items()):
        print(" ", coverage, decision, count)
    print("VALIDATED_PII_UNCOVERED:", sum(v.get("decision") == "VALID_PII" for v in decisions.values()))
    print("GOLD_UNCHANGED: true")


def get_mask(text, gold, predictor):
    from unified_v1.ax_veil_ablation import no_veil_counterfactual
    raw = predictor(text)
    prepared = no_veil_counterfactual(text, {"by_source": {"ax": raw, "veil": []}})
    a, b = gold["start"], gold["end"]
    covered = bytearray(b - a)
    for mask in prepared["mask_intervals"]:
        start, end = max(a, mask["start"]), min(b, mask["end"])
        if start < end:
            covered[start-a:end-a] = b"\x01" * (end-start)
    return "".join("█" if value else "·" for value in covered)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--coverage", choices=("FULL", "PARTIAL", "BOTH"), default="BOTH")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--context", type=int, default=75)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    if not (1 <= args.limit <= 100 and 0 <= args.context <= 1500):
        parser.error("--limit must be 1..100 and --context 0..1500")
    data, audit, destination = (p.expanduser().resolve() for p in (args.data, args.audit, args.decisions))
    if destination in {data, audit}:
        parser.error("decisions must not overwrite dataset or audit")
    digest, docs, cases = validate(data, audit)
    saved = load_decisions(destination, digest, cases)
    if not args.review:
        print_summary(cases, saved)
        return
    predictor = None
    if args.checkpoint:
        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo / "scripts"))
        sys.path.insert(0, str(repo / "step2_privacy_rag" / "app"))
        from ax_v7_eval import predictor as make_predictor
        checkpoint = args.checkpoint.expanduser().resolve()
        if not (checkpoint / "config.json").is_file():
            raise ValueError("CHECKPOINT_NOT_FOUND")
        predictor = make_predictor(checkpoint, "cpu")
    print("LOCAL SENSITIVE VIEW — DO NOT SHARE TERMINAL OUTPUT")
    print("Decision: 1=VALID_PII 2=INVALID_GOLD 3=POLICY_DISPUTED 4=INSUFFICIENT_CONTEXT")
    print("Cause: 1=MODEL_NO_OVERLAP 2=BOUNDARY 3=LABEL_CONFLICT 4=MASK_GAP 5=GOLD_POLICY 6=UNKNOWN")
    displayed = 0
    for i, case in enumerate(cases):
        if args.coverage != "BOTH" and case["coverage"] != args.coverage:
            continue
        key = cid(case)
        if key in saved["decisions"]:
            continue
        doc = docs[case["document_index"]]
        gold = doc["entities"][case["gold_index"]]
        text = doc["text"]
        a, b = gold["start"], gold["end"]
        print(f"\nCASE_INDEX={i} | {case['coverage']} | {case['label']} | protected={case['covered_chars']}/{b-a}")
        print("GOLD_LOCAL:", repr(text[a:b]))
        print("CONTEXT_LOCAL:", repr(text[max(0,a-args.context):min(len(text),b+args.context)]))
        if predictor:
            pattern = get_mask(text, gold, predictor)
            if pattern.count("█") != case["covered_chars"]:
                raise ValueError("MASK_DIFFERS_FROM_PINNED_AUDIT")
            print("MASK_PATTERN (█ protected, · exposed):", pattern)
        displayed += 1
        choice = input("decision [1/2/3/4/s/q]: ").strip().lower()
        if choice == "q":
            break
        if choice not in DECISIONS:
            if displayed >= args.limit:
                break
            continue
        cause = input("cause [1/2/3/4/5/6/s]: ").strip().lower()
        if cause not in CAUSES:
            if displayed >= args.limit:
                break
            continue
        saved["decisions"][key] = {
            "decision": DECISIONS[choice], "cause": CAUSES[cause],
            "coverage": case["coverage"], "label": case["label"]
        }
        save_decisions(destination, saved)
        print("DECISION_SAVED:", i, DECISIONS[choice], CAUSES[cause])
        if displayed >= args.limit:
            break
    print_summary(cases, saved)
    print("PRIVATE_DECISIONS_SAVED_TO:", destination)


if __name__ == "__main__":
    main()
