"""Deterministic controller branch tests, with invented values and mock lookup.

No detector, model, real dataset, database, network or API. These test decisions
given detector receipts, NOT model recall, true identity, live ACL or egress.
Known integration gaps are reported separately and must not count as passes.
"""
from __future__ import annotations
from collections import Counter
import json
from starter.s2_query_controller import prepare_query,from_detector,_key
from starter.s2_pseudonymizer import READY,REVIEW,V7_POLICY,REGION_ALIASES,make_hmac_token

KEY=b"x"*32
def span(a,b,label):return dict(start=a,end=b,label=label)
def receipt(typed,events=(),**overrides):
    events=list(events);masks=[*typed,*events]
    return dict(policy=V7_POLICY,source="v7_context_validated",deployment_approved=False,
        status=REVIEW if events else READY,review_events=events,review=events,
        audit_events=[],entities=typed,raw_spans=typed,core_mask_intervals=masks,
        mask_intervals=masks,uncertainty_candidates=events,unmapped_non_o_uncovered_tokens=0,
        **{})|overrides

def run(text,typed,*,events=(),tokens=(),regions=(),meta=None,lookup=None,key=KEY):
    return prepare_query(text,typed,receipt(typed,events) if meta is None else meta,
        key=key,lookup=set(tokens).__contains__ if lookup is None else lookup,
        region_lookup=set(regions).__contains__)

def tok(label,value):return make_hmac_token(KEY,label,value)[0]
def check(name,result,expected,stats,*,gap=False):
    ok=result.status==expected
    # Validate safe local response and absence of authorization independently.
    if result.external_transfer_approved is not False:raise RuntimeError("EGRESS_FLAG")
    if result.status in {"CLARIFY","ERROR","INVALID_INPUT"}:
        if result.output is not None or not result.answer:raise RuntimeError("UNSAFE_RESPONSE")
    elif result.output is None or result.answer is not None:raise RuntimeError("MISSING_OUTPUT")
    stats["gap_cases" if gap else "branch_cases"]+=1
    stats[("gap_resolved" if ok else "gap_open") if gap else ("passed" if ok else "failed")]+=1
    print("DECISION_CASE",json.dumps(dict(case=name,expected=expected,actual=result.status,
        reason=result.reason,kind="integration_gap" if gap else "branch",pass_case=ok),ensure_ascii=False),flush=True)

def main():
    c=Counter()
    def ck(n,r,e,**kw):check(n,r,e,c,**kw)
    v="0000000000000";rr=tok("resident_registration_number",v);acct=tok("account_number",v)
    split=[span(0,2,"card_number"),span(2,13,"resident_registration_number")]
    full=[span(0,13,"resident_registration_number")]
    ck("general",run("업무 처리 기준",[]),"SEARCH_READY")
    for label,value in [("person","가상민"),("email","user@example.invalid"),("address","서울 강남구 가상로 10"),("phone_number","010-0000-0000"),("card_number","0000-0000-0000-0000"),("account_number","1234-0000-0000")]:
        ck("normal_"+label,run(value,[span(0,len(value),label)],tokens=[tok(label,value)]),"SEARCH_READY")
    ck("normal_rrn",run(v,full,tokens=[rr]),"SEARCH_READY")
    ck("split_repair",run(v,split,tokens=[rr]),"RECOVERED_LOCAL")
    ck("multiple_numeric_types",run(v,split,tokens=[rr,acct]),"CLARIFY")
    ck("no_scoped_match",run(v,split),"CLARIFY")
    ck("uncovered_digits",run(v,split[:1],tokens=[rr]),"CLARIFY")
    prefix="계좌번호 ";shifted=[span(x["start"]+len(prefix),x["end"]+len(prefix),x["label"]) for x in split]
    ck("explicit_prefix_mismatch",run(prefix+v,shifted,tokens=[rr]),"CLARIFY")
    ck("explicit_prefix_match",run(prefix+v,shifted,tokens=[rr,acct]),"RECOVERED_LOCAL")
    text="서울 강남구 상담 사례";ev=[span(3,6,"address")]
    region=_key(KEY,REGION_ALIASES["서울"]+"/강남구")
    ck("region_recovery",run(text,[],events=ev,regions=[region]),"RECOVERED_LOCAL")
    ck("region_unmatched",run(text,[],events=ev),"CLARIFY")
    ck("person_review",run("가상민 상담",[],events=[span(0,3,"person")]),"CLARIFY")
    ck("email_review",run("user@example.invalid 관련",[],events=[span(0,20,"email")]),"CLARIFY")
    ck("unmapped",run("업무 처리 기준",[],meta=receipt([],unmapped_non_o_uncovered_tokens=1)),"CLARIFY")
    ck("missing_mask",run(v,split,meta=receipt(split,mask_intervals=[]),tokens=[rr]),"ERROR")
    ck("bad_policy",run(v,split,meta=receipt(split,policy="unexpected"),tokens=[rr]),"ERROR")
    ck("weak_key",run(v,split,key=b"short",tokens=[rr]),"ERROR")
    ck("reserved_marker",run("<PERSON_fake>",[]),"INVALID_INPUT")
    ck("empty_input",run("",[]),"INVALID_INPUT")
    def failed_lookup(_):raise RuntimeError("synthetic lookup failure")
    ck("lookup_failure",run(v,split,lookup=failed_lookup),"ERROR")
    class BrokenDetector:
        def detect(self,_):raise RuntimeError("synthetic detector failure")
    ck("detector_failure",from_detector(v,BrokenDetector(),key=KEY,lookup=lambda _:True),"ERROR")
    # These are explicit limitations of the first controller version, not waived
    # production requirements. Later condition resolution must close them.
    ck("explicit_suffix_conflict",run(v+"은 계좌번호입니다",split,tokens=[rr]),"CLARIFY",gap=True)
    ck("multiple_region_conditions",run("서울 강남구 및 부산 해운대구 상담 사례",[],events=ev,regions=[region]),"CLARIFY",gap=True)
    print("QUERY_DECISION_TEST_SUMMARY",json.dumps(dict(c),ensure_ascii=False),flush=True)
    if c["failed"]:raise SystemExit(1)

if __name__=="__main__":main()
