"""Local projection grammar fixtures, with synthetic detector spans.
No model or corpus. Tests retention of actual name spans as negative controls.
"""
from collections import Counter
import json
from starter.s2_project_projection import project,case_nominal,has_address_detail
from starter.s2_pseudonymizer import READY,pseudonymize_local

KEY=b"x"*32
def person(a,b):return dict(start=a,end=b,label="person")
def test(name,text,typed,expected,*,audit=(),pending=()):
    old=pseudonymize_local(text,dict(decision=READY,entities=typed,entities_for_pseudonymization=typed,
        mask_intervals=typed,redaction_intervals=[]),hmac_key=KEY,include_vault_records=True)
    new=project(text,old,dict(audit_events=list(audit),review_events=list(pending)),key=KEY,query=True)
    released=[(e["start"],e["end"]) for e in new.released]
    assert released==expected,name
    assert new.strict is old and new.external_transfer_approved is False,name
    assert [(e["start"],e["end"],e["token"]) for e in new.search.safe_token_manifest]==[
        (e["start"],e["end"],e["token"]) for e in old.safe_token_manifest
        if (e["start"],e["end"]) not in expected],name
    print("CASE_GRAMMAR_TEST",json.dumps(dict(case=name,released=len(released),pass_case=True)),flush=True)

def main():
    for name,text,role in [
        ("case_copula","가상민 고객 건입니다, 기록 요청","고객 "),
        ("case_polite","가상민 신청인 건인데요, 문서 요청","신청인 "),
        ("case_compact","가상민 고객건입니다.","고객"),
        ("case_role_honorific","가상민 고객님 건이에요.","고객님 ")]:
        a=3+1+len(role)
        assert text[a:a+1]=="건"
        test(name,text,[person(0,3),person(a,a+1)],[(a,a+1)])
    test("standalone_given_name","건 고객님 기록",[person(0,1)],[])
    test("full_name_with_geon","가상 건 고객님 기록",[person(0,4)],[])
    test("separate_person_after_punctuation","가상민 고객, 건입니다.",[person(0,3),person(8,9)],[])
    test("no_previous_person","고객 건입니다.",[person(3,4)],[])
    test("name_followed_by_honorific","가상민 고객 건 님 기록",[person(0,3),person(7,8)],[])
    test("other_single_character","가상민 고객 윤입니다.",[person(0,3),person(7,8)],[])
    test("explicit_name_field","가상민 고객 성명: 건입니다.",[person(0,3),person(11,12)],[])
    text="가상민 고객 건입니다."
    test("overlapping_review",text,[person(0,3),person(7,8)],[],pending=[dict(start=7,end=8,label="person",source="test")])
    test("overlapping_audit",text,[person(0,3),person(7,8)],[],audit=[dict(start=7,end=8,label="person",source="unknown")])
    print("CASE_GRAMMAR_TEST_PASS",13,flush=True)

if __name__=="__main__":main()
