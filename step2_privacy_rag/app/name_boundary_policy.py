"""AegisRAG read-only, evidence-first Korean name-boundary experiment.

Policy V3.1 is a DIAGNOSTIC proposal (multi-label validation fix). It never modifies detector results,
changes REVIEW/BLOCKED status, creates identity tokens or authorizes export.
No gold labels/doc IDs/list of specific names are used by this policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

HANGUL = re.compile(r"^[가-힣]{2,6}$")
ALLOWED_LABELS = frozenset({
    "person", "phone_number", "email", "address", "resident_registration_number",
    "account_number", "card_number",
})
TITLE = (r"(?:님|씨|대리|과장|차장|부장|팀장|주임|실장|이사|사원|"
         r"선임|책임|상담사|심사역|담당자|매니저|드림|올림)"
         r"(?:께서|에게서|에게|이|가|은|는|을|를|의|도|께|와|과|랑)?(?![가-힣])")
DELIMITERS = set(" \n\r\t,.;:!?()[]{}<>\"'·、。|/\\")


@dataclass(frozen=True)
class MentionProposal:
    original_span: tuple[int, int]
    candidate_full_span: tuple[int, int]
    identity_span: tuple[int, int] | None
    action: str
    evidence: str
    protect_span: tuple[int, int] | None


def _valid_span(span: Any, text: str) -> bool:
    return (isinstance(span, dict) and span.get("label") in ALLOWED_LABELS
            and type(span.get("start")) is int and type(span.get("end")) is int
            and 0 <= span["start"] < span["end"] <= len(text))


def _title_count(text: str, name: str) -> int:
    if not HANGUL.fullmatch(name):
        return 0
    # Every name is escaped. The title must be immediately following a name
    # (optional whitespace), so a random substring does not count as evidence.
    pattern = r"(?<![가-힣])" + re.escape(name) + r"\s*" + TITLE
    return len(list(re.finditer(pattern, text)))


def _has_batchim(char: str) -> bool:
    return len(char) == 1 and '가' <= char <= '힣' and (ord(char)-0xAC00) % 28 != 0


def _candidate_from_entity(text: str, start: int, end: int) -> tuple[int, int] | None:
    name = text[start:end]
    if name.endswith("이") and len(name) >= 3:
        long_end = end
    elif end < len(text) and text[end] == "이" and (end+1 == len(text) or text[end+1] in DELIMITERS):
        long_end = end + 1
    else:
        return None
    long_name = text[start:long_end]
    short_name = text[start:long_end-1]
    if not HANGUL.fullmatch(long_name) or not HANGUL.fullmatch(short_name):
        return None
    return start, long_end


def merge_intervals(intervals: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    if any(type(a) is not int or type(b) is not int or not (0 <= a < b <= n)
           for a, b in intervals):
        raise ValueError("invalid_interval")
    sorted_ints = sorted(intervals)
    result: list[tuple[int,int]] = []
    for a, b in sorted_ints:
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def baseline_masks(text: str, detector_result: dict) -> list[tuple[int,int]]:
    masks = detector_result.get("mask_intervals")
    if not isinstance(masks, list):
        raise ValueError("missing_mask_intervals")
    intervals: list[tuple[int,int]] = []
    for m in masks:
        if not isinstance(m, dict) or type(m.get("start")) is not int or type(m.get("end")) is not int:
            raise ValueError("bad_mask_interval")
        intervals.append((m["start"], m["end"]))
    return merge_intervals(intervals, len(text))


def propose(text: str, detector_result: dict) -> list[MentionProposal]:
    """Read-only proposals for suffix-'이' anchors, including READY cases.

    Evidence tier: explicit honorific/job-title occurrence of the long vs short
    name within the same document. Korean morphology is intentionally NOT used
    alone to decide identity: it only identifies possible grammatical ambiguity.

    Protection may extend to the FULL contiguous name candidate. This is only a
    proposal, NOT a mask output to feed to DB, Gate, HMAC, or an external API.
    """
    if detector_result.get("status") not in {"READY_FOR_PSEUDONYMIZATION", "REVIEW_REQUIRED"}:
        return []
    spans = detector_result.get("entities")
    if not isinstance(spans, list) or not isinstance(detector_result.get("mask_intervals"), list):
        return []
    result: list[MentionProposal] = []
    seen: set[tuple[int,int]] = set()
    for e in spans:
        if not _valid_span(e, text):
            return []  # fail closed on malformed entities of ANY label
        if e["label"] != "person":
            continue  # legitimate non-PERSON PII does not cancel name proposals
        original = (e["start"], e["end"])
        cand = _candidate_from_entity(text, *original)
        if not cand or cand in seen:
            continue
        seen.add(cand)
        s, t = cand
        long_name, short_name = text[s:t], text[s:t-1]
        long_evidence = _title_count(text, long_name)
        short_evidence = _title_count(text, short_name)
        # Direct title evidence is only provisional: names can be homonymous.
        if long_evidence and short_evidence:
            action, identity, ev = "AMBIGUOUS_PROTECT_ONLY", None, "conflicting_title_evidence"
        elif long_evidence:
            action, identity, ev = "EVIDENCE_LONG_PROPOSED", (s, t), "long_title_evidence"
        elif short_evidence:
            action, identity, ev = "EVIDENCE_SHORT_PROPOSED", (s, t-1), "short_title_evidence"
        else:
            # Weak morphology is only recorded, never resolves identity.
            action, identity = "AMBIGUOUS_PROTECT_ONLY", None
            ev = "batchim_weak_evidence" if _has_batchim(short_name[-1]) else "no_strong_evidence"
        result.append(MentionProposal(original, cand, identity, action, ev, cand))
    return result


def proposal_masks(text: str, detector_result: dict, proposals: list[MentionProposal]) -> list[tuple[int,int]]:
    baseline = baseline_masks(text, detector_result)
    return merge_intervals(baseline + [p.protect_span for p in proposals if p.protect_span], len(text))


def proposed_person_spans(detector_result: dict, proposals: list[MentionProposal]) -> list[tuple[int,int]]:
    """A simulation for scoring only: not an edited detector response."""
    entities = detector_result.get("entities")
    if not isinstance(entities, list):
        return []
    original = [(int(e["start"]),int(e["end"])) for e in entities
                if isinstance(e, dict) and e.get("label") == "person"]
    mapping = {p.original_span:p.identity_span for p in proposals if p.identity_span is not None}
    return sorted(set(mapping.get(x, x) for x in original))
