"""Local query preparation v1. No retrieval, identity approval, or egress.
The caller supplies an ACL-scoped token/region lookup, not global existence.
A local candidate requires downstream condition, ownership and ACL validation.
Never log result.output, candidate keys, raw text, or unexpected exceptions.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import hmac
import re
from .s2_pseudonymizer import (
    READY, REVIEW, BLOCKED, REGION_ALIASES, V7_POLICY, prepare_from_detection,
    pseudonymize_local, make_hmac_token, PseudonymizationError,
    canonicalize_address_v22,
)
from .s2_numeric_recovery import NUMERIC, plans

# Reuse the V6 region hierarchy (province + city/district + optional subdistrict).
_PROVINCES="|".join(re.escape(x) for x in sorted(REGION_ALIASES,key=len,reverse=True))
_REGION=re.compile(rf"(?<![가-힣])(?P<province>{_PROVINCES})\s+(?P<city>[가-힣0-9]{{1,12}}(?:시|군|구))(?=$|\s|[,.(]|에|의|에서|관련|거주)")
_SUB=re.compile(r"\s+(?P<sub>[가-힣0-9]{1,12}(?:구|군))(?=$|\s|[,.(]|에|의|에서|관련|거주)")
RECOVERABLE={"invalid_number_value","invalid_phone_value","invalid_account_value"}
MESSAGES={
    "address":"어떤 지역이나 주소의 정보를 찾으시는지 조금 더 구체적으로 알려주시겠어요?",
    "person":"어떤 분의 정보를 찾으시는지 이름이나 업무 관련 단서를 확인해 주세요.",
    "numeric":"번호의 종류나 입력 형식을 확인해 주세요. 다른 업무 관련 단서를 알려주셔도 됩니다.",
    "generic":"질문의 검색 대상을 정확히 확인하기 어렵습니다. 찾으시는 정보나 업무 조건을 조금 더 구체적으로 알려주세요.",
    "error":"현재 질문을 안전하게 처리하지 못했습니다. 다시 시도해 주세요.",
    "invalid":"질문을 다시 입력해 주세요.",
}
@dataclass(frozen=True,repr=False)
class QueryDecision:
    status:str
    reason:str
    answer:str|None=None
    output:object|None=None
    region_keys:tuple[str,...]=()
    external_transfer_approved:bool=False
    # SEARCH_READY/RECOVERED_LOCAL are NOT retrieval or answer authorization.

def clarify(kind,reason):
    return QueryDecision("CLARIFY",reason,MESSAGES[kind])

def _key(key,path):
    return "region_v1_"+hmac.new(key,b"AEGISRAG:REGION:V1\x00"+path.encode(),hashlib.sha256).hexdigest()[:24]

def region_paths(text):
    out=[]
    for m in _REGION.finditer(text):
        base=REGION_ALIASES[m.group("province")]+"/"+m.group("city")
        sub=_SUB.match(text,m.end()) if m.group("city").endswith("시") else None
        out.append((m.start(),sub.end() if sub else m.end(),base+"/"+sub.group("sub") if sub else base))
    return out

def document_region_keys(text,manifest,key):
    out=set()
    for e in manifest:
        if e["label"]!="address":continue
        value=canonicalize_address_v22(text[e["start"]:e["end"]])
        for a,b,p in region_paths(value):
            if a!=0:continue
            out.add(_key(key,p))
            if p.count("/")==2:out.add(_key(key,p.rsplit("/",1)[0]))
    return tuple(sorted(out))

def overlaps(a,b,e):
    return a<e["end"] and e["start"]<b

def _review_kind(events):
    labels={e.get("label") for e in events}
    if labels=={"address"}:return "address"
    if labels=={"person"}:return "person"
    if labels and labels<=set(NUMERIC):return "numeric"
    return "generic"

def _rebuild(text,typed,meta,key):
    # Preserve the entire original protection union. Never fake a new model
    # receipt, unmask an audit span or approve egress.
    return pseudonymize_local(text,dict(decision=READY,entities=typed,
        entities_for_pseudonymization=typed,mask_intervals=meta["mask_intervals"],
        redaction_intervals=[]),hmac_key=key,include_vault_records=True)

def _plan(text,typed,meta,key,lookup,region_lookup):
    base=prepare_from_detection(text,typed,meta,hmac_key=key,include_vault_records=True)
    if base.decision==BLOCKED and base.reason not in RECOVERABLE:
        if base.reason in {"invalid_text","reserved_marker_in_input","invalid_email_value"}:
            return QueryDecision("INVALID_INPUT",base.reason,MESSAGES["invalid"])
        return QueryDecision("ERROR",base.reason,MESSAGES["error"])
    if base.decision==REVIEW:
        events=meta["review_events"]
        if meta["unmapped_non_o_uncovered_tokens"] or not events:
            return clarify("generic","unmapped_or_unknown_review")
        if any(type(e.get("start")) is not int or type(e.get("end")) is not int
               or not 0<=e["start"]<e["end"]<=len(text) for e in events):
            return QueryDecision("ERROR","bad_review_offsets",MESSAGES["error"])
        if _review_kind(events)=="address":
            # Only an observed full regional hierarchy, exact ACL-scoped key,
            # and all unresolved spans inside that hierarchy. No unmasking.
            paths=region_paths(text)
            if len(paths)!=1:
                return clarify("address","ambiguous_region_conditions")
            matches=[(a,b,_key(key,p)) for a,b,p in paths
                     if all(a<=e["start"]<e["end"]<=b for e in events)
                     and not any(overlaps(a,b,e) for e in typed)]
            if len(matches)==1 and region_lookup(matches[0][2]):
                a,b,k=matches[0]
                # Restrict this recovery to a region-only PII condition.
                # Other typed identifiers need the later joint-condition resolver.
                if not typed:
                    output=_rebuild(text,typed,meta,key)
                    return QueryDecision("RECOVERED_LOCAL","region_candidate",output=output,region_keys=(k,))
        return clarify(_review_kind(events),"unresolved_detector_review")
    # Only a validated READY result or a recognized numeric conversion failure
    # can reach the recovery planner. Other failures never bypass the gate.
    ps=plans(text,typed,[],key=key,lookup=lookup)
    if any(p.status=="CLARIFY" for p in ps):
        return clarify("numeric","numeric_condition_unresolved")
    # Fail closed on number shapes outside the planner (masked/foreign etc.)
    # unless an exact typed key exists; no new type inference for those shapes.
    for e in typed:
        if e["label"] not in NUMERIC:continue
        if any(p.start<=e["start"]<e["end"]<=p.end for p in ps):continue
        try:t,_=make_hmac_token(key,e["label"],text[e["start"]:e["end"]])
        except PseudonymizationError:return clarify("numeric","unsupported_numeric_value")
        if not lookup(t):return clarify("numeric","unsupported_numeric_unmatched")
    changes=[p for p in ps if p.status=="CANDIDATE"]
    if changes:
        revised=[e for e in typed if not any(overlaps(p.start,p.end,e) for p in changes)]
        revised.extend(dict(start=p.start,end=p.end,label=p.label) for p in changes)
        # Source union must still cover every character in new spans.
        # pseudonymize_local rejects otherwise, including uncovered separators.
        output=_rebuild(text,sorted(revised,key=lambda e:e["start"]),meta,key)
        return QueryDecision("RECOVERED_LOCAL","numeric_candidate",output=output)
    if base.decision!=READY:return clarify("numeric","numeric_conversion_failed")
    return QueryDecision("SEARCH_READY","local_prepared",output=base.output)

def prepare_query(text,typed,meta,*,key,lookup,region_lookup=lambda _:False):
    try:return _plan(text,typed,meta,key,lookup,region_lookup)
    except PseudonymizationError:
        return clarify("generic","recovery_protection_failed")
    except Exception:
        return QueryDecision("ERROR","query_processing_failed",MESSAGES["error"])

def from_detector(text,detector,*,key,lookup,region_lookup=lambda _:False):
    try:typed,meta=detector.detect(text)
    except Exception:return QueryDecision("ERROR","detector_failed",MESSAGES["error"])
    return prepare_query(text,typed,meta,key=key,lookup=lookup,region_lookup=region_lookup)

def self_test():
    key=b"x"*32
    def meta(typed,masks=None,events=None):
        masks=typed if masks is None else masks;events=events or []
        return dict(policy=V7_POLICY,source="v7_context_validated",deployment_approved=False,
                    status=REVIEW if events else READY,review_events=events,review=events,
                    audit_events=[],entities=typed,raw_spans=typed,core_mask_intervals=masks,
                    mask_intervals=masks,uncertainty_candidates=events,unmapped_non_o_uncovered_tokens=0)
    def run(text,typed,md=None,tokens=(),regions=()):
        return prepare_query(text,typed,md or meta(typed),key=key,lookup=set(tokens).__contains__,
                             region_lookup=set(regions).__contains__)
    v="0000000000000";t,_=make_hmac_token(key,"resident_registration_number",v)
    a,_=make_hmac_token(key,"account_number",v)
    typed=[dict(start=0,end=2,label="card_number"),dict(start=2,end=13,label="resident_registration_number")]
    r=run(v,typed,tokens=[t]);assert r.status=="RECOVERED_LOCAL" and r.output.sanitized_text==t
    assert run(v,typed,tokens=[t,a]).status=="CLARIFY"
    assert run(v,typed).status=="CLARIFY"
    assert run(v,typed,meta(typed,[]),tokens=[t]).status=="ERROR"
    assert run("일반 업무 기준",[]).status=="SEARCH_READY"
    assert run("<PERSON_fake>",[]).status=="INVALID_INPUT"
    text="서울 강남구 상담 사례";ev=[dict(start=3,end=6,label="address",source="test")]
    m=meta([],ev,ev);k=_key(key,REGION_ALIASES["서울"]+"/강남구")
    r=run(text,[],m,regions=[k]);assert r.status=="RECOVERED_LOCAL" and "강남구" not in r.output.sanitized_text
    assert run(text,[],m).status=="CLARIFY"
    assert run(text,[],{**m,"unmapped_non_o_uncovered_tokens":1},regions=[k]).status=="CLARIFY"
    assert not r.external_transfer_approved
    print("QUERY_CONTROLLER_SELF_TEST_PASS",flush=True)

if __name__=="__main__":self_test()
