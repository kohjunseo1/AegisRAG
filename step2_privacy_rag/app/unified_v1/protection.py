"""Evidence-preserving coverage and a conservative adapter around frozen V6 Gate."""
from __future__ import annotations
from dataclasses import replace
import re
from privacy_gate import check_privacy_gate
from dataset_preparation import key_tag as frozen_key_tag
from local_pseudonymizer import pseudonymize_local
from .contracts import (Candidate, ContractError, DetectionEvidence, PreparationResult,
                        ProtectionPlan, fingerprint, PROFILES)

POLICY = 'evidence_v1'
READY = 'READY_FOR_LOCAL_PSEUDONYMIZATION'

def merge_intervals(intervals, n):
    out = []
    for start, end in sorted(intervals):
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= n:
            raise ContractError('INVALID_OFFSET')
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start,end))
    return tuple(out)

def covers(interval, masks):
    return any(a <= interval[0] < interval[1] <= b for a,b in masks)

def overlap(a,b):
    return a.start < b.end and b.start < a.end

class StructuralSentinel:
    patterns = (
        ('phone_number', re.compile(r'(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)'), 'PHONE_FORMAT'),
        ('email', re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'), 'EMAIL_FORMAT'),
        ('resident_registration_number', re.compile(r'(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)'), 'RRN_REQUIRES_VALIDATION'),
        ('address', re.compile(r'(?m)(?<=우편번호[:：])\s*\d{5}(?!\d)'), 'POSTCODE_CONTEXT'),
        ('address', re.compile(r'(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|전라|경상|제주)[가-힣]*(?:[ \t]+[가-힣]+[시군구동로길])+[ \t\d가-힣-]*'), 'ADDRESS_PARTIAL'),
        ('account_number', re.compile(r'(?<=계좌번호:)\s*\d[\d -]{5,25}\d'), 'ACCOUNT_FIELD_REQUIRES_VALIDATION'),
        ('card_number', re.compile(r'(?<=카드번호:)\s*\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}'), 'CARD_FIELD_REQUIRES_VALIDATION'),
    )

    def candidates(self,text):
        return tuple(Candidate('structural','sentinel-v1',label,m.start(),m.end(),fingerprint(text),
                               evidence_codes=(reason,)).validate(text)
                     for label,pattern,reason in self.patterns for m in pattern.finditer(text))

class ContextualBoost:
    # Generic field/honorific proposals. No actual-person list, stripping or identity merge.
    patterns = (
        re.compile(r'(?:고객명|성명|담당자|이름)\s*[:：]\s*([가-힣](?:[ \t]?[가-힣]){0,4})(?=[\s,;]|$)'),
        re.compile(r'(?<![가-힣])([가-힣]{1,2}[ \t]+[가-힣]{1,2})(?=[ \t]*(?:님|씨|과장|팀장)(?![가-힣]))'),
        re.compile(r'(?<![가-힣])([가-힣]{1,4})[ \t]+(?:님|씨|과장|팀장)(?![가-힣])'),
    )

    def candidates(self,text,confirmed_persons=()):
        rows = tuple(Candidate('contextual','boost-v1','person',m.start(1),m.end(1),fingerprint(text),
                               evidence_codes=('NAME_CONTEXT_UNCONFIRMED',)).validate(text)
                     for p in self.patterns for m in p.finditer(text))
        repeats=[]
        for name in confirmed_persons:
            compact=re.sub(r'\s+','',text[name.start:name.end])
            if not re.fullmatch(r'[가-힣]{1,5}',compact):continue
            pattern=re.compile(r'(?<![가-힣])'+r'[ \t]*'.join(map(re.escape,compact))+
                               r'(?=(?:께서|에게|에서|이는|이를|이의|은|는|이|가|을|를|의|님|씨)?(?:[^가-힣]|$))')
            for m in pattern.finditer(text):
                repeats.append(Candidate('contextual','boost-v1','person',m.start(),m.end(),fingerprint(text),
                                         evidence_codes=('REPEATED_NAME_CANDIDATE_ONLY',)).validate(text))
        return rows+tuple(repeats)

class V6GateAdapter:
    def __init__(self, baseline_detector):
        self.baseline_detector = baseline_detector

    def check(self,text):
        # Existing V6 logic alone decides its baseline readiness.
        return check_privacy_gate(self.baseline_detector,text)

    def plan(self,text,evidence):
        return fuse(text,evidence,self.check(text))

def fuse(text: str, evidence: DetectionEvidence, baseline_gate: dict) -> ProtectionPlan:
    reasons = []
    if (evidence.input_kind not in {'document','query'} or not evidence.request_id
            or evidence.original_input_fingerprint != fingerprint(text)
            or set(evidence.raw_candidates_by_source) != set(PROFILES.get(evidence.detector_profile,()))):
        raise ContractError('INVALID_DETECTION_EVIDENCE')
    if not evidence.ready:
        reasons.append('REQUIRED_MODEL_UNAVAILABLE_OR_FAILED')
    if baseline_gate.get('decision') != READY:
        reasons.append('BASELINE_GATE_NOT_READY')
    masks = [(s['start'],s['end']) for s in baseline_gate.get('mask_intervals',[])]
    masks += [(s['start'],s['end']) for s in baseline_gate.get('proposed_protection',[])]
    redactions = tuple((s['start'],s['end']) for s in baseline_gate.get('redaction_intervals',[]))
    strict, hmac_entities = [], []
    for name,target in [('entities',strict),('entities_for_pseudonymization',hmac_entities)]:
        for s in baseline_gate.get(name,[]):
            c=Candidate('ax','v6-gate-baseline',s['label'],s['start'],s['end'],fingerprint(text),
                        evidence_codes=('FROZEN_GATE_VALIDATED',)).validate(text)
            target.append(c)
            if not covers((c.start,c.end),merge_intervals(masks,len(text))):
                raise ContractError('BASELINE_ENTITY_NOT_COVERED')
    baseline_masks = merge_intervals(masks,len(text))
    all_candidates = [c for rows in evidence.raw_candidates_by_source.values() for c in rows]
    all_candidates += list(evidence.structural_candidates)
    all_candidates += list(StructuralSentinel().candidates(text)) + list(ContextualBoost().candidates(text,[c for c in hmac_entities if c.label=='person']))
    unresolved = []
    for c in all_candidates:
        c.validate(text)
        masks.append((c.start,c.end))
        exact = any(c.label == e.label and c.start == e.start and c.end == e.end for e in hmac_entities)
        compatible_coverage = any(c.label == e.label and e.start <= c.start < c.end <= e.end for e in hmac_entities)
        conflicting = any(overlap(c,e) and c.label != e.label for e in hmac_entities)
        if conflicting:
            reasons.append('LABEL_CONFLICT')
            unresolved.append(c)
        elif not (exact or compatible_coverage):
            # Even consensus does not validate canonical value/label; preserve protection and review.
            unresolved.append(c)
            reasons.append('UNCONFIRMED_CANDIDATE')
    for i,c in enumerate(hmac_entities):
        if any(overlap(c,e) for e in hmac_entities[i+1:]):
            reasons.append('HMAC_BOUNDARY_CONFLICT')
    hmac_entities = [e for e in hmac_entities if not any(overlap(e,c) for c in unresolved)]
    final_masks = merge_intervals(masks + list(redactions),len(text))
    if any(not covers(m,final_masks) for m in baseline_masks):
        raise ContractError('BASELINE_COVERAGE_SHRANK')
    if any(not covers((e.start,e.end),final_masks) for e in strict+hmac_entities):
        raise ContractError('HMAC_NOT_COVERED')
    return ProtectionPlan(tuple(strict),tuple(hmac_entities),final_masks,redactions,tuple(unresolved),
                          tuple(sorted(set(reasons))),evidence.detector_profile,POLICY,fingerprint(text),evidence.input_kind)

def prepare(text, plan, *, input_kind, hmac_key, key_tag):
    if (input_kind not in {'document','query'} or input_kind != plan.input_kind
            or plan.original_input_fingerprint != fingerprint(text)):
        raise ContractError('PREPARATION_INPUT_MISMATCH')
    if type(hmac_key) is not bytes or len(hmac_key) < 32 or key_tag != frozen_key_tag(hmac_key):
        raise ContractError('HMAC_KEY_TAG_MISMATCH')
    if plan.review_reason_codes:
        blocked = 'REQUIRED_MODEL_UNAVAILABLE_OR_FAILED' in plan.review_reason_codes
        return PreparationResult('BLOCKED' if blocked else 'REVIEW','NONE','NOT_APPROVED',None,
                                 key_tag,plan.policy_version,input_kind,plan.review_reason_codes)
    gate = {'decision':READY, 'entities':[dict(label=c.label,start=c.start,end=c.end) for c in plan.strict_entities],
            'entities_for_pseudonymization':[dict(label=c.label,start=c.start,end=c.end) for c in plan.entities_for_hmac],
            'mask_intervals':[dict(start=a,end=b) for a,b in plan.mask_intervals],
            'redaction_intervals':[dict(start=a,end=b) for a,b in plan.redaction_intervals]}
    try:
        result=pseudonymize_local(text,gate,hmac_key=hmac_key,include_vault_records=False)
    except Exception:
        raise ContractError('LOCAL_PROTECTION_FAILED') from None
    kind = 'MASKED_ONLY' if result.residual_redaction_count or (plan.mask_intervals and not plan.entities_for_hmac) else 'LINKED'
    return PreparationResult('LOCAL_PREPARED',kind,'NOT_APPROVED',result.sanitized_text,key_tag,
                             plan.policy_version,input_kind,coverage_verified=True)
