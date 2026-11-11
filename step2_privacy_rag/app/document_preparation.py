"""AegisRAG in-memory, local-only Step1 -> V5 -> Gate -> Pseudonymizer contract.

Does NOT certify that all PII was detected; outputs sanitized data only in memory.
No file/network/DB IO. No raw content or secrets in exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from pathlib import Path
from typing import Any

from name_protection_handoff import (
    STEP1_READY, GATE_READY, _FrozenDetectorView, _protection_plan,
)
from name_protection_policy import decide
from name_boundary_policy import propose

_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_TOKEN = re.compile(r'<[A-Z_]+_[0-9a-f]{24}>')

@dataclass(frozen=True)
class Outcome:
    doc_id: str
    state: str
    reason: str
    sanitized_text: str | None = None   # IN-MEMORY ONLY: may still contain missed PII
    tokens: tuple[str, ...] = ()
    unlinked_mentions: int = 0
    region_keys: tuple[str, ...] = ()


def prepare(doc_id: str, text: str, detector: Any, key: bytes) -> Outcome:
    if not isinstance(doc_id, str) or not _ID.fullmatch(doc_id):
        raise ValueError('invalid_id')
    if not isinstance(text, str) or not isinstance(key, bytes) or len(key) < 32:
        return Outcome(doc_id, 'BLOCKED', 'INVALID_INPUT')
    try:
        result = detector.detect(text)
        if not isinstance(result, dict):
            return Outcome(doc_id,'BLOCKED','INVALID_DETECTOR_RESULT')
        if result.get('status') != STEP1_READY:
            state = 'REVIEW' if result.get('status')=='REVIEW_REQUIRED' else 'BLOCKED'
            return Outcome(doc_id,state,'STEP1_NOT_READY')
        decision = decide(text, result, 'v5_privacy_first')
        if decision.get('would_abstain'):
            return Outcome(doc_id,'REVIEW','V5_ABSTAIN')
        proposals = propose(text,result)
        from privacy_gate import check_privacy_gate
        g = check_privacy_gate(_FrozenDetectorView(detector,result), text)
        if not isinstance(g,dict) or g.get('decision')!=GATE_READY:
            return Outcome(doc_id,'REVIEW','GATE_NOT_READY')
        g,counts = _protection_plan(text,result,g,decision,proposals)
        from region_keys import keys_from_gate, keys_from_query
        region_keys = set(keys_from_gate(text, g, key))
        if doc_id.startswith('Q'):
            region_keys.update(keys_from_query(text, key))
        from local_pseudonymizer import pseudonymize_local
        p = pseudonymize_local(text,g,hmac_key=key,include_vault_records=False)
        # These contract checks do not prove that unrecognized PII is absent.
        if p.vault_records is not None or not isinstance(p.sanitized_text,str):
            return Outcome(doc_id,'BLOCKED','INVALID_PSEUDONYMIZER_RESULT')
        tokens=tuple(sorted(set(_TOKEN.findall(p.sanitized_text))))
        return Outcome(doc_id,'LOCAL_PREPARED','LOCAL_ONLY_NOT_EXTERNAL_AUTHORIZED',
                       p.sanitized_text,tokens,counts['nonlinked_name_mentions'],tuple(sorted(region_keys)))
    except Exception:
        # Never echo exceptions: upstream errors may include raw PII.
        return Outcome(doc_id,'BLOCKED','SAFE_PIPELINE_FAILURE')
