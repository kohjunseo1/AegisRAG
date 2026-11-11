"""AegisRAG Step 2 Privacy Gate V3 (local pseudonymization permission only).

Keeps Step 1 strict entities unchanged. Only auto-resolves one narrowly confirmed
PERSON gap pattern: two AX PERSON spans separated by one ASCII space, where
Veil identifies the entire three-Hangul-syllable name as one PERSON.

It does NOT authorize external embedding/LLM calls, guarantee full PII coverage,
or perform HMAC/Vault writes. Unresolved model disagreements must stay local.
"""
from __future__ import annotations

import re

READY = "READY_FOR_LOCAL_PSEUDONYMIZATION"
VALID_LABELS = frozenset({
    "person", "phone_number", "email", "address",
    "resident_registration_number", "account_number", "card_number",
})
SPACED_NAME = re.compile(r"^[가-힣] [가-힣]{2}$")


def check_spans(spans, n, require_label):
    if not isinstance(spans, list):
        raise ValueError("invalid_spans")
    out = []
    for span in spans:
        if not isinstance(span, dict):
            raise ValueError("invalid_span")
        start, end = span.get("start"), span.get("end")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= n):
            raise ValueError("invalid_offsets")
        clean = {"start": start, "end": end}
        if require_label:
            if span.get("label") not in VALID_LABELS:
                raise ValueError("invalid_label")
            clean["label"] = span["label"]
        out.append(clean)
    return out


def merge_masks(spans):
    result = []
    for span in sorted(spans, key=lambda s: (s["start"], s["end"])):
        if result and span["start"] <= result[-1]["end"]:
            result[-1]["end"] = max(result[-1]["end"], span["end"])
        else:
            result.append({"start": span["start"], "end": span["end"]})
    return result


def covered(span, masks):
    return any(m["start"] <= span["start"] and m["end"] >= span["end"]
               for m in masks)


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def find_safe_name_merges(text, strict_entities, veil_persons, base_masks):
    """Conservative *candidate* resolution; not a generic Korean-name merger."""
    persons = [p for p in strict_entities if p["label"] == "person"]
    proposals = []
    used = set()
    for veil in sorted(veil_persons, key=lambda s: (s["start"], s["end"])):
        within = [p for p in persons
                  if veil["start"] <= p["start"] and p["end"] <= veil["end"]]
        if len(within) != 2:
            continue
        a, b = sorted(within, key=lambda s: s["start"])
        if (a["start"] != veil["start"] or b["end"] != veil["end"]
                or text[a["end"]:b["start"]] != " "
                or not SPACED_NAME.fullmatch(text[veil["start"]:veil["end"]])):
            continue
        if not (covered(a, base_masks) and covered(b, base_masks)):
            continue
        if any(overlaps(other, veil) for other in strict_entities
               if other is not a and other is not b):
            continue
        keys = {(p["label"], p["start"], p["end"]) for p in (a, b)}
        if used & keys or any(overlaps(veil, old["merged"]) for old in proposals):
            continue
        used.update(keys)
        proposals.append({"merged": dict(veil), "replace": keys})
    return proposals


def check_privacy_gate(detector, text):
    if not isinstance(text, str):
        return {"decision": "BLOCKED", "reason": "invalid_input"}
    try:
        result = detector.detect(text)
        if not isinstance(result, dict):
            raise ValueError("invalid_result")
        status = result.get("status")
        if status != "READY_FOR_PSEUDONYMIZATION":
            return {"decision": ("REVIEW_REQUIRED" if status == "REVIEW_REQUIRED" else "BLOCKED"),
                    "reason": "detector_not_ready"}
        strict = check_spans(result.get("entities"), len(text), True)
        masks = merge_masks(check_spans(result.get("mask_intervals"), len(text), False))
        for ent in strict:
            if not covered(ent, masks):
                return {"decision": "BLOCKED", "reason": "entity_not_covered"}
        ax_spans = check_spans(detector.ax_predict(text), len(text), True)
        veil_spans = check_spans(detector.veil_predict(text), len(text), True)
        # Address candidates from A.X may have been discarded by frozen fusion.
        ax_missing = [s for s in ax_spans
                      if s["label"] == "address" and not covered(s, masks)]
        if ax_missing:
            return {"decision": "REVIEW_REQUIRED", "reason": "unprotected_ax_address",
                    "proposed_protection": merge_masks(masks + ax_missing),
                    "uncovered_count": len(ax_missing)}
        veil_persons = [s for s in veil_spans if s["label"] == "person"]
        merges = find_safe_name_merges(text, strict, veil_persons, masks)
        merged_persons = [m["merged"] for m in merges]
        expanded_masks = merge_masks(masks + merged_persons)
        # Any other Veil-person candidate not covered after repairs is ambiguous.
        veil_uncovered = [s for s in veil_persons if not covered(s, expanded_masks)]
        if veil_uncovered:
            return {"decision": "REVIEW_REQUIRED", "reason": "unprotected_veil_person",
                    "proposed_protection": merge_masks(expanded_masks + veil_uncovered),
                    "uncovered_count": len(veil_uncovered)}
        replaced = set().union(*(p["replace"] for p in merges)) if merges else set()
        resolved = [s for s in strict
                    if (s["label"], s["start"], s["end"]) not in replaced]
        resolved.extend(merged_persons)
        resolved.sort(key=lambda s: (s["start"], s["end"], s["label"]))
        if any(not covered(s, expanded_masks) for s in resolved):
            raise ValueError("unprotected_resolved_entity")
        # Strict entities are unchanged, while resolved entities are prospective
        # single-token HMAC inputs. Every residual mask must still be redacted.
        return {
            "decision": READY,
            "reason": "person_gap_repaired" if merges else "candidates_covered",
            "entities": strict,
            "entities_for_pseudonymization": resolved,
            "mask_intervals": expanded_masks,
            "person_merges": len(merges),
        }
    except Exception:
        # Never leak exception text or raw document into logs/results.
        return {"decision": "BLOCKED", "reason": "gate_validation_failed"}
