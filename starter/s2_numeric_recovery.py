"""Experimental local numeric query candidate planner; no approval or egress.

Lookup is an ACL-scoped exact-token existence function supplied by the caller.
A candidate is NOT an identity decision. Gold is never passed to this module.
Do not use this to infer arbitrary unlabeled numbers or drop user constraints.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata
from .s2_pseudonymizer import make_hmac_token, PseudonymizationError

NUMERIC=("phone_number","resident_registration_number","card_number","account_number")
RUN=re.compile(r"(?<![0-9A-Za-z*+.-])\+?[0-9]+(?:[-. ]+[0-9]+)*(?![0-9A-Za-z*+.-])")
# Explicit field cues immediately adjacent to the number, not anywhere in the query.
CUE=re.compile(r"(전화번호|휴대전화번호|휴대폰번호|연락처|주민등록번호|주민번호|카드\s*번호|계좌\s*번호)\s*[:：]?\s*$")
CUE_LABEL={"전화번호":"phone_number","휴대전화번호":"phone_number",
           "휴대폰번호":"phone_number","연락처":"phone_number",
           "주민등록번호":"resident_registration_number","주민번호":"resident_registration_number",
           "카드번호":"card_number","계좌번호":"account_number"}
SUFFIX_CUE=re.compile(r"^\s*(?:은|는|이|가)?\s*(전화번호|휴대전화번호|휴대폰번호|연락처|주민등록번호|주민번호|카드\s*번호|계좌\s*번호)(?=\s*(?:입니다|이다|이에요|예요|임|[,.?!]|$))")
PHONE=re.compile(r"(?:01[016789][0-9]{7,8}|02[0-9]{7,8}|0[3-6][1-5][0-9]{7,8}|070[0-9]{8}|050[0-9]{8,9})\Z")

@dataclass(frozen=True,repr=False)
class NumericPlan:
    start:int
    end:int
    status:str
    reason:str
    label:str|None=None
    token:str|None=None
    matched_labels:tuple[str,...]=()

def eligible(value):
    if not re.fullmatch(r"\+?[0-9]+(?:[-. ]+[0-9]+)*",value):
        return ()
    digits=re.sub(r"[^0-9]","",value)
    labels=[]
    try:
        phone=make_hmac_token(b"\0"*32,"phone_number",value)[1]
        if PHONE.fullmatch(phone):labels.append("phone_number")
    except PseudonymizationError:pass
    # Format filters only. Neither length nor a lookup hit establishes type/owner.
    if not value.startswith("+"):
        if len(digits)==13:labels.append("resident_registration_number")
        if 13<=len(digits)<=19:labels.append("card_number")
        if 7<=len(digits)<=24:labels.append("account_number")
    return tuple(labels)

def overlap(a,b,e):
    return a<e["end"] and e["start"]<b

def plans(text,typed,review,*,key,lookup):
    """Return provisional local candidates for complete observed numeric runs.

    No guessing from unrelated tokens; no merging across unobserved numbers.
    Original query text/offsets and V7 output are immutable.
    Explicit prefix contradictions, unresolved review and unsupported formats defer.
    """
    out=[]
    for m in RUN.finditer(text):
        a,b=m.span();v=m.group()
        spans=[e for e in typed if overlap(a,b,e)]
        pending=[e for e in review if overlap(a,b,e)]
        if not any(e.get("label") in NUMERIC for e in spans+pending):continue
        if pending or any(e.get("label") not in NUMERIC or e["start"]<a or e["end"]>b for e in spans):
            out.append(NumericPlan(a,b,"CLARIFY","overlapping_uncertainty"));continue
        if not spans:continue
        # No expansion into uncovered digits. Spaces between independent typed
        # fields are not evidence that they form one identifier.
        covered=set(i for e in spans for i in range(e["start"],e["end"]))
        if any(i not in covered for i in range(a,b) if text[i].isdigit()):
            out.append(NumericPlan(a,b,"CLARIFY","uncovered_digits"));continue
        if len(spans)>1 and any(c in " ." for c in v):
            out.append(NumericPlan(a,b,"CLARIFY","ambiguous_join"));continue
        labels=eligible(v)
        cue=CUE.search(text[:a])
        explicit=CUE_LABEL[re.sub(r"\s+","",cue.group(1))] if cue else None
        suffix=SUFFIX_CUE.match(text[b:])
        suffix_label=CUE_LABEL[re.sub(r"\s+","",suffix.group(1))] if suffix else None
        if explicit and suffix_label and explicit!=suffix_label:
            out.append(NumericPlan(a,b,"CLARIFY","conflicting_type_cues"));continue
        explicit=explicit or suffix_label
        hits={}
        for label in labels:
            t,_=make_hmac_token(key,label,v)
            if lookup(t):hits[label]=t
        matched=tuple(sorted(hits))
        exact=len(spans)==1 and spans[0]["start"]==a and spans[0]["end"]==b
        original=spans[0]["label"] if exact else None
        # Explicit constraints cannot be overridden by a different-type hit.
        if explicit and explicit not in hits:
            out.append(NumericPlan(a,b,"CLARIFY","explicit_type_unmatched",matched_labels=matched));continue
        if not explicit and len(hits)>1:
            out.append(NumericPlan(a,b,"CLARIFY","multiple_type_matches",matched_labels=matched));continue
        chosen=explicit or (matched[0] if len(matched)==1 else None)
        if chosen is None:
            out.append(NumericPlan(a,b,"CLARIFY","no_supported_exact_match",matched_labels=matched));continue
        status="UNCHANGED" if original==chosen else "CANDIDATE"
        out.append(NumericPlan(a,b,status,"exact_local_token_match",chosen,hits[chosen],matched))
    return tuple(out)

def self_test():
    key=b"x"*32
    # Invented shape-only examples; no external data or actual identity decisions.
    value="0000000000000"
    t,_=make_hmac_token(key,"resident_registration_number",value)
    typed=[dict(start=0,end=2,label="card_number"),dict(start=2,end=13,label="resident_registration_number")]
    p=plans(value,typed,[],key=key,lookup={t}.__contains__)
    assert len(p)==1 and p[0].status=="CANDIDATE" and p[0].label=="resident_registration_number"
    a,_=make_hmac_token(key,"account_number",value)
    assert plans(value,typed,[],key=key,lookup={t,a}.__contains__)[0].reason=="multiple_type_matches"
    assert plans(value,typed,[],key=key,lookup=lambda _:False)[0].status=="CLARIFY"
    assert plans(value,typed[:1],[],key=key,lookup={t}.__contains__)[0].reason=="uncovered_digits"
    assert plans(value,typed,[dict(start=2,end=3,label="address")],key=key,lookup={t}.__contains__)[0].status=="CLARIFY"
    prefix="계좌번호 "
    shifted=[{**e,"start":e["start"]+len(prefix),"end":e["end"]+len(prefix)} for e in typed]
    assert plans(prefix+value,shifted,[],key=key,lookup={t}.__contains__)[0].reason=="explicit_type_unmatched"
    assert plans(prefix+value,shifted,[],key=key,lookup={t,a}.__contains__)[0].label=="account_number"
    assert plans(value,[],[],key=key,lookup={t}.__contains__)==()
    print("NUMERIC_PLAN_SELF_TEST_PASS",flush=True)

if __name__=="__main__":self_test()
