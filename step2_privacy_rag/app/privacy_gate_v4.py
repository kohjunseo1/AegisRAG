"""AegisRAG Privacy Gate V4 -- local-only candidate triage (NOT external approval).

Keeps frozen Step 1 detector and V3 unchanged. Adds explicitly scoped
Veil-only PERSON decisions: IGNORE_EXTRA_CANDIDATE / REDACT_ROLE_REFERENCE /
REVIEW_REQUIRED. Never drops an existing strict entity or mask.

Important contract: READY means *local processing only*. The downstream local
pseudonymizer MUST apply `redaction_intervals` AND all residual mask positions;
otherwise it MUST fail closed. This module performs no redaction or HMAC.
"""
from __future__ import annotations

from collections import Counter

from privacy_gate_v3 import (
    READY, check_spans, merge_masks, covered, overlaps, find_safe_name_merges,
)
from veil_person_policy import (
    classify_veil_only_person, IGNORE, REDACT, REVIEW,
)


def _review(reason, masks, ambiguous, counts):
    # Diagnostic protection is not approval for any transfer or write.
    return {
        "decision": "REVIEW_REQUIRED",
        "reason": reason,
        "proposed_protection": merge_masks(masks + ambiguous),
        "uncovered_count": len(ambiguous),
        "veil_policy_counts": dict(counts),
    }


def check_privacy_gate(detector, text):
    if not isinstance(text, str):
        return {"decision": "BLOCKED", "reason": "invalid_input"}
    try:
        result = detector.detect(text)
        if not isinstance(result, dict):
            raise ValueError("invalid_result")
        status = result.get("status")
        if status != "READY_FOR_PSEUDONYMIZATION":
            return {
                "decision": "REVIEW_REQUIRED" if status == "REVIEW_REQUIRED" else "BLOCKED",
                "reason": "detector_not_ready",
            }

        strict = check_spans(result.get("entities"), len(text), True)
        masks = merge_masks(check_spans(result.get("mask_intervals"), len(text), False))
        if any(not covered(e, masks) for e in strict):
            return {"decision": "BLOCKED", "reason": "entity_not_covered"}
        ax = check_spans(detector.ax_predict(text), len(text), True)
        veil = check_spans(detector.veil_predict(text), len(text), True)

        # V3's A.X address check must remain BEFORE the Veil-only policy.
        missing_ax_address = [x for x in ax if x["label"] == "address" and not covered(x, masks)]
        if missing_ax_address:
            return {
                "decision": "REVIEW_REQUIRED", "reason": "unprotected_ax_address",
                "proposed_protection": merge_masks(masks + missing_ax_address),
                "uncovered_count": len(missing_ax_address),
            }

        veil_person = [x for x in veil if x["label"] == "person"]
        merges = find_safe_name_merges(text, strict, veil_person, masks)
        merged_persons = [m["merged"] for m in merges]
        expanded = merge_masks(masks + merged_persons)
        uncovered = [s for s in veil_person if not covered(s, expanded)]

        # Explicit, individual redaction spans, distinct from HMAC entities.
        role_redactions = []
        counts = Counter()
        rejected = []
        for candidate in uncovered:
            policy = classify_veil_only_person(text, candidate["start"], candidate["end"])

            # An ignore rule may NEVER override existing Strict entities, or
            # contradict an additional A.X address candidate.
            conflicts = (
                any(overlaps(candidate, e) for e in strict)
                or any(overlaps(candidate, e) for e in ax if e["label"] == "address")
            )
            if policy.action == IGNORE and not conflicts:
                counts["ignored"] += 1
                counts["ignore:" + policy.reason] += 1
                continue

            if policy.action == REDACT and not conflicts:
                start, end = policy.protection_start, policy.protection_end
                if (type(start) is int and type(end) is int
                        and 0 <= start < end <= len(text)
                        and start <= candidate["start"] < candidate["end"] <= end):
                    proposal = {"start": start, "end": end}
                    # No automatic role redaction that overlaps a strict PII
                    # object or already merged HMAC entity; identity unclear.
                    if (not any(overlaps(proposal, e) for e in strict)
                            and not any(overlaps(proposal, e) for e in merged_persons)
                            and not any(overlaps(proposal, e) for e in role_redactions)):
                        role_redactions.append(proposal)
                        counts["role_redacted"] += 1
                        counts["redact:" + policy.reason] += 1
                        continue

            counts["ambiguous"] += 1
            rejected.append(candidate)

        if rejected:
            # All candidates are included in diagnostic proposed protection;
            # IGNORED candidates have no authority to remove existing masks.
            return _review(
                "unresolved_veil_person",
                expanded + role_redactions,
                rejected,
                counts,
            )

        replaced = set().union(*(p["replace"] for p in merges)) if merges else set()
        resolved = [s for s in strict if (s["label"], s["start"], s["end"]) not in replaced]
        resolved.extend(merged_persons)
        resolved.sort(key=lambda s: (s["start"], s["end"], s["label"]))
        final_masks = merge_masks(expanded + role_redactions)
        if any(not covered(s, final_masks) for s in resolved):
            raise ValueError("hmac_entity_not_covered")
        if any(not covered(s, final_masks) for s in role_redactions):
            raise ValueError("role_redaction_not_covered")

        return {
            "decision": READY,
            "reason": "veil_candidates_triaged" if uncovered else (
                "person_gap_repaired" if merges else "candidates_covered"
            ),
            "entities": strict,  # preserve Step 1 strict spans unchanged
            "entities_for_pseudonymization": resolved,
            "mask_intervals": final_masks,
            "redaction_intervals": role_redactions,
            "person_merges": len(merges),
            "veil_policy_counts": dict(counts),
            "note": "LOCAL_ONLY; downstream must redact mask-only positions before any transfer",
        }
    except Exception:
        # Never emit exceptions that might contain raw document text.
        return {"decision": "BLOCKED", "reason": "gate_validation_failed"}
