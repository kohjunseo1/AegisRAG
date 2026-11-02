"""AegisRAG Step1: minimal, versioned, deterministic Korean PII policy.

Deploy AFTER the existing detector.validate_boundaries(). Two suppression
rules only: a standalone one-syllable surname followed by a job title, and
an explicit short internal-extension field. Do not change model outputs or
annotation boundaries elsewhere. No gold/holdout data used at prediction time.

IMPORTANT: entities are strict evaluation spans. mask_intervals protect the
UNION of original and revised spans; consumers MUST pseudonymize using the
mask intervals, NOT the refined entities alone.
"""
from __future__ import annotations
import re
from typing import Iterable

POLICY_VERSION = "aegis_core_v1"
LABELS = frozenset({
    "person", "phone_number", "email", "address",
    "resident_registration_number", "account_number", "card_number"
})
# Fixed language-level title and ordinary Korean surname sets; never dataset-specific names.
TITLE = (r"(?:과장|대리|팀장|부장|차장|사원|주임|책임|선임|수석|대표|이사|"
         r"상무|전무|실장|본부장|센터장|매니저)")
# IMPORTANT: [ \t] instead of \s to avoid crossing lines.
SURNAME_TITLE_AFTER = re.compile(
    rf"^[ \t]+{TITLE}(?:님)?(?:에게|께서|께|은|는|이|가|을|를|과|와|도)?(?=$|[ \t\r\n,.;:!?)\]])"
)
SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공"
    "현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부가복태목형피두감음빈동온호"
)
EXTENSION_VALUE = re.compile(
    r"(?:\b내선(?:번호)?|\(\s*내선\s*\))\s*[:：]\s*([0-9 ]{2,8})(?=\s*$)"
)


def _key(e: dict, text: str) -> tuple[str, int, int]:
    if not isinstance(e, dict):
        raise ValueError("invalid_span_type")
    lab = e.get("label")
    if lab not in LABELS:
        raise ValueError("invalid_span_label")
    s, end = e.get("start"), e.get("end")
    if type(s) is not int or type(end) is not int or not 0 <= s < end <= len(text):
        raise ValueError("invalid_span_offset")
    return lab, s, end


def _line(text: str, start: int, end: int) -> tuple[str, int]:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return text[a:len(text) if b < 0 else b], a


def _standalone_surname_title(text: str, start: int, end: int) -> bool:
    word = text[start:end]
    if len(word) != 1 or word not in SURNAMES:
        return False
    # A character within a longer word is never a standalone surname.
    if start > 0 and "가" <= text[start-1] <= "힣":
        return False
    return SURNAME_TITLE_AFTER.match(text[end:end+28]) is not None


def _explicit_internal_extension(text: str, start: int, end: int) -> bool:
    val = text[start:end]
    if not val.isascii() or not val.isdecimal() or not 2 <= len(val) <= 6:
        return False
    line, origin = _line(text, start, end)
    match = EXTENSION_VALUE.search(line)
    if match is None:
        return False
    return origin + match.start(1) <= start and end <= origin + match.end(1)


def refine_spans(text: str, spans: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return (strict entity spans, audit without source PII strings)."""
    if not isinstance(text, str):
        raise ValueError("invalid_text")
    out, audit, seen = [], [], set()
    for raw in spans:
        lab, start, end = _key(raw, text)
        key = (lab, start, end)
        if key in seen:
            continue
        seen.add(key)
        rule = None
        if lab == "person" and _standalone_surname_title(text, start, end):
            rule = "standalone_surname_title"
        elif lab == "phone_number" and _explicit_internal_extension(text, start, end):
            rule = "explicit_internal_extension"
        if rule:
            audit.append({"rule": rule, "action": "drop_strict_only", "label": lab,
                          "start": start, "end": end})
        else:
            out.append({"label": lab, "start": start, "end": end})
    return sorted(out, key=lambda e: (e["start"], e["end"], e["label"])), audit


def mask_union(original: Iterable[dict], revised: Iterable[dict], text: str) -> list[dict]:
    """Union coverage. Never unmask a character detected by the frozen baseline."""
    intervals = []
    for e in [*original, *revised]:
        _, s, en = _key(e, text)
        intervals.append((s, en))
    intervals.sort()
    merged = []
    for s, en in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((s, en))
    return [{"start": s, "end": en} for s, en in merged]


def attach_policy(text: str, detector_result: dict) -> dict:
    """Wrapper for the actual repo detector.detect() output.

    Does not promote REVIEW_REQUIRED/BLOCKED. BLOCKED remains unchanged.
    Any exception must be handled FAIL-CLOSED by the caller (not ignored).
    """
    if not isinstance(detector_result, dict):
        raise ValueError("invalid_detector_result")
    result = dict(detector_result)
    if result.get("status") == "BLOCKED":
        return result
    if result.get("status") not in ("READY_FOR_PSEUDONYMIZATION", "REVIEW_REQUIRED"):
        raise ValueError("unknown_status")
    original = list(result.get("entities", ()))
    revised, changes = refine_spans(text, original)
    result["entities"] = revised
    result["mask_intervals"] = mask_union(original, revised, text)
    result["structural_policy_events"] = changes
    result["policy_version"] = POLICY_VERSION
    # Never upgrade status or remove review issues.
    return result
