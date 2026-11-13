"""AegisRAG in-memory, local-only Step1 -> V5 -> Gate -> Pseudonymizer contract.

Does NOT certify that all PII was detected; outputs sanitized data only in memory.
No file/network/DB IO. No raw content or secrets in exceptions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import traceback
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
    stage='step1_detector'
    try:
        result = detector.detect(text)
        if not isinstance(result, dict):
            return Outcome(doc_id,'BLOCKED','INVALID_DETECTOR_RESULT')
        if result.get('status') != STEP1_READY:
            state = 'REVIEW' if result.get('status')=='REVIEW_REQUIRED' else 'BLOCKED'
            return Outcome(doc_id,state,'STEP1_NOT_READY')
        stage='v5_name_policy'
        decision = decide(text, result, 'v5_privacy_first')
        if decision.get('would_abstain'):
            return Outcome(doc_id,'REVIEW','V5_ABSTAIN')
        stage='name_boundary_proposals'
        proposals = propose(text,result)
        from privacy_gate import check_privacy_gate
        stage='privacy_gate'
        g = check_privacy_gate(_FrozenDetectorView(detector,result), text)
        if not isinstance(g,dict) or g.get('decision')!=GATE_READY:
            if os.environ.get('AEGISRAG_LOCAL_FAILURE_DIAG')=='1':
                allowed_reasons={'unprotected_ax_address','unresolved_veil_person',
                                 'entity_not_covered','detector_not_ready','gate_validation_failed',
                                 'invalid_input'}
                raw_reason=g.get('reason') if isinstance(g,dict) else None
                print('BLIND_SAFE_GATE_DIAG',json.dumps({
                    'case':doc_id,
                    'gate_decision':g.get('decision') if isinstance(g,dict)
                         and g.get('decision') in {'BLOCKED','REVIEW_REQUIRED'} else 'UNKNOWN',
                    'gate_reason':raw_reason if raw_reason in allowed_reasons else 'UNCLASSIFIED'
                },sort_keys=True),flush=True)
            return Outcome(doc_id,'REVIEW','GATE_NOT_READY')
        stage='name_protection_plan'
        g,counts = _protection_plan(text,result,g,decision,proposals)
        stage='region_key_derivation'
        from region_keys import keys_from_gate, keys_from_query
        region_keys = set(keys_from_gate(text, g, key))
        if doc_id.startswith('Q'):
            region_keys.update(keys_from_query(text, key))
        from local_pseudonymizer import pseudonymize_local
        stage='local_pseudonymization'
        p = pseudonymize_local(text,g,hmac_key=key,include_vault_records=False)
        # These contract checks do not prove that unrecognized PII is absent.
        if p.vault_records is not None or not isinstance(p.sanitized_text,str):
            return Outcome(doc_id,'BLOCKED','INVALID_PSEUDONYMIZER_RESULT')
        tokens=tuple(sorted(set(_TOKEN.findall(p.sanitized_text))))
        return Outcome(doc_id,'LOCAL_PREPARED','LOCAL_ONLY_NOT_EXTERNAL_AUTHORIZED',
                       p.sanitized_text,tokens,counts['nonlinked_name_mentions'],tuple(sorted(region_keys)))
    except Exception as exc:
        # Opt-in local diagnostics log ONLY bounded code metadata. Never emit
        # exception messages, document text, HMAC keys, local variables or frames.
        if os.environ.get('AEGISRAG_LOCAL_FAILURE_DIAG')=='1':
            tb=traceback.extract_tb(exc.__traceback__)
            leaf=tb[-1] if tb else None
            allowed={'name_span_collision','name_protection_incomplete',
                     'protection_regression','invalid_proposal','invalid_classification',
                     'invalid_address_span','address_not_protected',
                     'hmac_entity_not_covered','role_redaction_not_covered',
                     'invalid_result','invalid_spans','invalid_labels','invalid_offsets'}
            # PseudonymizationError only carries our static, PII-free error
            # codes. Still allowlist the code rather than exposing exc str.
            allowed.update({
                'invalid_input','gate_not_ready','missing_or_weak_hmac_key',
                'invalid_capture_flag','invalid_entity_label',
                'conflicting_entity_or_role_spans','unmasked_entity_or_role',
                'overlapping_replacement_jobs','incomplete_mask_coverage',
                'invalid_canonicalization_input','empty_canonical_value',
                'invalid_email_value','invalid_phone_value',
                'invalid_account_value','invalid_number_value',
                'unsupported_label','unknown_replacement_kind','token_collision'
            })
            raw_code=exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else None
            if type(exc).__name__=='PseudonymizationError' and raw_code:
                if re.fullmatch(r'invalid_(?:mask_intervals|hmac_entities|redaction_intervals|strict_entities)(?:_offsets)?',raw_code):
                    allowed.add(raw_code)
            code=raw_code if raw_code in allowed else 'UNCLASSIFIED'
            print('BLIND_SAFE_FAILURE',json.dumps({
                'case':doc_id,'stage':stage,'exception_type':type(exc).__name__,
                'code':code,'source_module':Path(leaf.filename).name if leaf else 'unknown',
                'source_line':leaf.lineno if leaf else 0,
            },sort_keys=True),flush=True)
        return Outcome(doc_id,'BLOCKED','SAFE_PIPELINE_FAILURE')
