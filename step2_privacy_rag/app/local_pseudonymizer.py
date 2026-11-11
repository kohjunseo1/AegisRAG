"""AegisRAG Step 2: fail-closed, offline HMAC pseudonymization (V1).

Contract:
  - Takes text and READY result from privacy_gate.check_privacy_gate.
  - No DB, model, network or filesystem I/O.
  - HMAC entity spans, redact role spans, redact *every* residual mask-only span.
  - Strictly validate spans, labels, ranges, overlaps and full mask coverage.
  - REVIEW/BLOCKED cannot be pseudonymized.
  - Does not claim to detect PII omitted by all input detectors.
  - A caller must not send sanitized text externally before independent tests.

This module may contain sensitive values *only in volatile memory*.  Do not print
sanitized_text or `vault_records` to logs; do not persist raw text via exceptions.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

READY = "READY_FOR_LOCAL_PSEUDONYMIZATION"
LABELS = frozenset({
    "person", "phone_number", "email", "address",
    "resident_registration_number", "account_number", "card_number",
})
DOMAIN_TAG = b"aegisrag:hmac-sha256:pseudonym:v1\x00"
HANGUL_NAME = re.compile(r"[가-힣]+")


class PseudonymizationError(ValueError):
    """Intentionally PII-free, short error codes only."""


def _fail(code: str) -> None:
    raise PseudonymizationError(code)


def _spans(value: Any, n: int, *, labeled: bool, name: str) -> list[dict]:
    if not isinstance(value, list):
        _fail("invalid_" + name)
    out = []
    for s in value:
        if not isinstance(s, dict):
            _fail("invalid_" + name)
        a, b = s.get("start"), s.get("end")
        if type(a) is not int or type(b) is not int or not (0 <= a < b <= n):
            _fail("invalid_" + name + "_offsets")
        item = {"start": a, "end": b}
        if labeled:
            if s.get("label") not in LABELS:
                _fail("invalid_entity_label")
            item["label"] = s["label"]
        out.append(item)
    return out


def _merge_intervals(spans: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in sorted(spans, key=lambda p: (p["start"], p["end"])):
        if out and s["start"] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        else:
            out.append({"start": s["start"], "end": s["end"]})
    return out


def _no_overlaps(items: list[dict]) -> bool:
    items = sorted(items, key=lambda s: (s["start"], s["end"]))
    return all(x["end"] <= y["start"] for x, y in zip(items, items[1:]))


def _covered(s: dict, masks: list[dict]) -> bool:
    return any(m["start"] <= s["start"] and s["end"] <= m["end"] for m in masks)



# Candidate V2.2: deliberately conservative; not a national address parser.
REGION_ALIASES = {
    '서울특별시': '서울특별시', '서울시': '서울특별시', '서울': '서울특별시',
    '부산광역시': '부산광역시', '부산시': '부산광역시', '부산': '부산광역시',
    '대구광역시': '대구광역시', '대구시': '대구광역시', '대구': '대구광역시',
    '인천광역시': '인천광역시', '인천시': '인천광역시', '인천': '인천광역시',
    '광주광역시': '광주광역시',  # bare 광주/광주시 is ambiguous
    '대전광역시': '대전광역시', '대전시': '대전광역시', '대전': '대전광역시',
    '울산광역시': '울산광역시', '울산시': '울산광역시', '울산': '울산광역시',
    '세종특별자치시': '세종특별자치시', '세종시': '세종특별자치시', '세종': '세종특별자치시',
    '경기도': '경기도', '경기': '경기도',
    '강원특별자치도': '강원특별자치도', '강원도': '강원특별자치도', '강원': '강원특별자치도',
    '충청북도': '충청북도', '충북': '충청북도',
    '충청남도': '충청남도', '충남': '충청남도',
    '전북특별자치도': '전북특별자치도', '전라북도': '전북특별자치도', '전북': '전북특별자치도',
    '전라남도': '전라남도', '전남': '전라남도',
    '경상북도': '경상북도', '경북': '경상북도',
    '경상남도': '경상남도', '경남': '경상남도',
    '제주특별자치도': '제주특별자치도', '제주도': '제주특별자치도', '제주': '제주특별자치도',
}


def canonicalize_address_v22(raw: str) -> str:
    s = unicodedata.normalize('NFKC', raw).strip()
    s = re.sub(r'\s+', ' ', s)
    head, sep, tail = s.partition(' ')
    if head in REGION_ALIASES:
        s = REGION_ALIASES[head] + (sep + tail if sep else '')
    s = re.sub(r'(?<=\d)\s*-\s*(?=\d)', '-', s)
    s = re.sub(r'(?<=[로길])(?=\d{1,5}(?:-\d{1,4})?(?=\s|$|\())', ' ', s)
    s = re.sub(r'\s*\(', ' (', s)
    s = re.sub(r'(?<!\d)(\d{1,3})\s*동\s*(\d{3,4})\s*호(?=\s|$)',
               r'\1동 \2호', s)
    # Only recognize compact apartment unit when an explicit street building
    # number appears before it. Ambiguous bare '101-1203' is not normalized.
    s = re.sub(r'((?:로|길|대로)\s+\d{1,5}(?:-\d{1,4})?\s+)'
               r'(\d{1,3})-(\d{3,4})(?=\s|$)', r'\1\2동 \3호', s)
    return re.sub(r'\s+', ' ', s).strip()

def canonicalize(label: str, value: str) -> str:
    """Conservative, label-specific canonicalization, not linguistic identity resolution.

    Only exact canonical equality yields the same token; distinct people with
    equal canonical names may collide semantically (requires separate identity
    disambiguation later). No user/gold name list or external services are used.
    """
    if label not in LABELS or not isinstance(value, str):
        _fail("invalid_canonicalization_input")
    val = unicodedata.normalize("NFKC", value).strip()
    if not val:
        _fail("empty_canonical_value")
    if label == "person":
        compact = re.sub(r"\s+", "", val)
        if HANGUL_NAME.fullmatch(compact):
            return compact  # '홍 길동' and '홍길동' => same, ONLY after Gate merges
        return re.sub(r"\s+", " ", val).casefold()
    if label == "email":
        if "@" not in val or val.count("@") != 1:
            _fail("invalid_email_value")
        local, domain = val.split("@")
        if not local or not domain or any(c.isspace() for c in val):
            _fail("invalid_email_value")
        # Email local-part is *not* universally case insensitive.
        return local + "@" + domain.casefold()
    if label == "address":
        return canonicalize_address_v22(val)
    if label == "phone_number":
        if not re.fullmatch(r"[+\d\s.()\-]+", val):
            _fail("invalid_phone_value")
        digits = re.sub(r"\D", "", val)
        if val.startswith("+82") or (digits.startswith("82") and len(digits) in (11, 12)):
            remainder = digits[2:]
            if remainder.startswith("0"):
                remainder = remainder[1:]
            if remainder:
                digits = "0" + remainder
        if len(digits) < 7:
            _fail("invalid_phone_value")
        return digits
    if label == "account_number":
        # IBANs can contain letters; never collapse unknown symbols silently.
        if not re.fullmatch(r"[A-Za-z0-9*\s\-]+", val):
            _fail("invalid_account_value")
        norm = re.sub(r"[\s\-]", "", val).upper()
        if len(norm) < 4:
            _fail("invalid_account_value")
        return norm
    if label in ("card_number", "resident_registration_number"):
        if not re.fullmatch(r"[0-9*\s.\-]+", val):
            _fail("invalid_number_value")
        norm = re.sub(r"[\s.\-]", "", val)
        if len(norm) < 4:
            _fail("invalid_number_value")
        return norm
    _fail("unsupported_label")


def make_hmac_token(key: bytes, label: str, raw_value: str) -> tuple[str, str]:
    """Return (safe_token, canonical_value); canonical value stays local only."""
    if type(key) is not bytes or len(key) < 32:
        _fail("missing_or_weak_hmac_key")
    norm = canonicalize(label, raw_value)
    payload = DOMAIN_TAG + label.encode("ascii") + b"\x00" + norm.encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest()[:24]  # 96-bit label-scoped token
    return f"<{label.upper()}_{digest}>", norm


@dataclass(frozen=True)
class PseudonymizationResult:
    sanitized_text: str
    hmac_entity_count: int
    role_redaction_count: int
    residual_redaction_count: int
    residual_redacted_chars: int
    replaced_source_chars: int
    # Labels and tokens only; contains no original or canonical PII values.
    safe_token_manifest: tuple[dict, ...]
    # ONLY when explicitly requested; raw and normalized PII are sensitive.
    vault_records: tuple[dict, ...] | None


def pseudonymize_local(
    text: str,
    gate_result: dict,
    *,
    hmac_key: bytes,
    include_vault_records: bool = False,
) -> PseudonymizationResult:
    """Transform every V4-protected source position, or fail closed.

    IMPORTANT: This is local-only conversion. Not an external transfer permit.
    Don't log `text`, `sanitized_text`, raw vault mappings or Python exceptions.
    """
    if not isinstance(text, str) or not isinstance(gate_result, dict):
        _fail("invalid_input")
    if gate_result.get("decision") != READY:
        _fail("gate_not_ready")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        _fail("missing_or_weak_hmac_key")
    if type(include_vault_records) is not bool:
        _fail("invalid_capture_flag")

    n = len(text)
    masks = _merge_intervals(_spans(gate_result.get("mask_intervals"), n, labeled=False, name="mask_intervals"))
    entities = _spans(gate_result.get("entities_for_pseudonymization"), n, labeled=True, name="hmac_entities")
    roles = _spans(gate_result.get("redaction_intervals"), n, labeled=False, name="redaction_intervals")
    strict = _spans(gate_result.get("entities"), n, labeled=True, name="strict_entities")

    source_spans = sorted(
        [{**e, "kind": "hmac"} for e in entities] +
        [{**r, "kind": "role"} for r in roles],
        key=lambda s: (s["start"], s["end"]),
    )
    if not _no_overlaps(source_spans):
        _fail("conflicting_entity_or_role_spans")
    if any(not _covered(s, masks) for s in source_spans + strict):
        _fail("unmasked_entity_or_role")

    # Compute all residual protected intervals, without scanning PII strings.
    residual = []
    for m in masks:
        cursor = m["start"]
        for s in source_spans:
            if s["end"] <= cursor:
                continue
            if s["start"] >= m["end"]:
                break
            if cursor < s["start"]:
                residual.append({"start": cursor, "end": s["start"], "kind": "residual"})
            cursor = max(cursor, min(s["end"], m["end"]))
        if cursor < m["end"]:
            residual.append({"start": cursor, "end": m["end"], "kind": "residual"})

    jobs = sorted(source_spans + residual, key=lambda s: (s["start"], s["end"]))
    if not _no_overlaps(jobs):
        _fail("overlapping_replacement_jobs")
    if _merge_intervals(jobs) != masks:
        _fail("incomplete_mask_coverage")

    out = []
    manifest = []
    vault = [] if include_vault_records else None
    cursor = 0
    seen_tokens = {}
    for s in jobs:
        a, b, kind = s["start"], s["end"], s["kind"]
        out.append(text[cursor:a])
        if kind == "hmac":
            label = s["label"]
            token, canonical = make_hmac_token(hmac_key, label, text[a:b])
            prev = seen_tokens.get(token)
            # Guard against truncation collisions or cross-label accidental aliases.
            current = (label, canonical)
            if prev is not None and prev != current:
                _fail("token_collision")
            seen_tokens[token] = current
            out.append(token)
            manifest.append({"label": label, "token": token, "start": a, "end": b})
            if vault is not None:
                vault.append({
                    "token": token, "label": label,
                    "canonical": canonical, "original": text[a:b],
                    "source_start": a, "source_end": b,
                })
        elif kind == "role":
            out.append("[ROLE_REFERENCE]")
        elif kind == "residual":
            out.append("[REDACTED_UNCLASSIFIED]")
        else:
            _fail("unknown_replacement_kind")
        cursor = b
    out.append(text[cursor:])

    return PseudonymizationResult(
        sanitized_text="".join(out),
        hmac_entity_count=len(entities),
        role_redaction_count=len(roles),
        residual_redaction_count=len(residual),
        residual_redacted_chars=sum(s["end"] - s["start"] for s in residual),
        replaced_source_chars=sum(s["end"] - s["start"] for s in jobs),
        safe_token_manifest=tuple(manifest),
        vault_records=tuple(vault) if vault is not None else None,
    )
