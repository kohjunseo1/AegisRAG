"""Frozen synthetic attribute-association challenge for O0/O1/O2.

No A187 data, Gold, V7 inference, DB or LLM. Synthetic perfect spans isolate
relation logic. Fixtures specify document-level association, not legal
ownership or identity authorization. Do not tune against this set and then
report it as independent test performance.
"""
import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict

from tools.benchmark_s2_identity_graph import team_source, require, emit
from tools.benchmark_s2_ownership import predict, grade, key, span, PERSON, EVENT, UNCERTAIN

# [[label|synthetic subject ID / - (event or org) / ? (uncertain)|surface]]
# Subject IDs and relation annotations are stripped before prediction.
FIXTURES = (
    ("field01", "field", "신청인: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값1]]\n주민등록번호: [[resident_registration_number|A|주민값1]]"),
    ("field02", "field", "대상 고객: [[person|A|가상민]]\n카드번호: [[card_number|A|카드값1]]\n변경 주소: [[address|A|주소값1]]\n처리 담당: [[person|B|가상윤]]"),
    ("field03", "field", "신청인: [[person|A|가상민]]\n대리 신청: 가족 대리\n변경 주소: [[address|?|주소값1]]\n주소 대상자: 미확인"),
    ("field04", "field", "대상 고객: [[person|A|가상민]]\n\n보증인 정보\n변경 주소: [[address|?|주소값1]]\n보증인 성명: 미기재"),
    ("field05", "field", "신청인: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값1]]\n대리인: [[person|B|가상윤]]\n연락처: [[phone_number|B|전화값2]]"),
    ("field06", "field", "대상 고객: [[person|A|가상민]]\n배송 정보: 수령인은 별도 지정\n배송지: [[address|?|주소값1]]\n수령인: 미확인"),
    ("field07", "field", "신청인: [[person|A|가상민]]\n구분: 회사 정보\n연락처: [[phone_number|-|전화값1]]\n담당 직원: [[person|B|가상윤]]"),
    ("field08", "field", "신청인: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값1]]\n\n대리인: [[person|B|가상윤]]\n주소: [[address|B|주소값1]]"),
    ("table01", "table", "성명 | 역할 | 연락처\n[[person|A|가상민]] | 고객 | [[phone_number|A|전화값1]]\n[[person|B|가상윤]] | 담당자 | [[phone_number|B|전화값2]]"),
    ("table02", "table", "신청인 | 대리인 | 대리인 연락처\n[[person|A|가상민]] | [[person|B|가상윤]] | [[phone_number|B|전화값1]]"),
    ("table03", "table", "신청인 | 대리인 | 신청인 연락처\n[[person|A|가상민]] | [[person|B|가상윤]] | [[phone_number|A|전화값1]]"),
    ("table04", "table", "고객 | 담당자 | 공용 상담번호\n[[person|A|가상민]] | [[person|B|가상윤]] | [[phone_number|-|전화값1]]"),
    ("table05", "table", "성명 | 연락처 | 비고\n[[person|A|가상민]] | [[phone_number|A|전화값1]] | 본인\n[[person|B|가상민]] | [[phone_number|B|전화값2]] | 별도 고객"),
    ("mail01", "email", "받는 사람: [[email|A|메일값1]]\n제목: 안내\n\n[[person|A|가상민]] 님, 안녕하세요.\n일정을 안내드립니다."),
    ("mail02", "email", "To: [[email|A|메일값1]]\nDear [[person|A|가상민]],\nPlease confirm your appointment.\nBest regards,\n[[person|B|가상윤]]"),
    ("mail03", "email", "To: [[email|?|메일값1]], [[email|?|메일값2]]\nDear [[person|A|가상민]],\nEither recipient may reply. Individual addresses are not identified."),
    ("mail04", "email", "To: [[email|-|메일값1]]\n제목: 공용 접수함\n\n[[person|A|가상민]] 님, 안녕하세요.\n이 메일은 팀 공용 접수함으로 보냅니다."),
    ("mail05", "email", "To: [[email|B|메일값1]]\nSubject: Forwarded message\n----- 전달 원문 -----\nDear [[person|A|가상민]],\n이전 요청을 전달합니다.\n현재 수신자: [[person|B|가상윤]]"),
    ("mail06", "email", "수신자: [[email|A|메일값1]]\n제목: 일정 확인\n\n[[person|A|가상민]] 님, 안녕하세요.\n보내는 사람: [[person|B|가상윤]]"),
    ("sign01", "signoff", "Dear [[person|A|가상민]],\nPlease call me at [[phone_number|B|전화값1]].\nBest regards,\n[[person|B|가상윤]]"),
    ("sign02", "signoff", "Dear [[person|A|가상민]],\nQuoted customer message: \"call me at [[phone_number|A|전화값1]].\"\nBest regards,\n[[person|B|가상윤]]"),
    ("sign03", "signoff", "Dear [[person|A|가상민]],\nDo not call me at [[phone_number|-|전화값1]]. This is the shared helpdesk number.\nBest regards,\n[[person|B|가상윤]]"),
    ("sign04", "signoff", "Dear [[person|A|가상민]],\nPlease call me at [[phone_number|B|전화값1]].\nRegards,\n[[person|B|가상윤]]\nForwarded by: [[person|C|가상진]]"),
    ("sign05", "signoff", "Dear [[person|A|가상민]],\nCall me at [[phone_number|?|전화값1]].\nRegards,\n[[person|B|가상윤]]\nRegards,\n[[person|C|가상진]]\n발신자 확인 불가"),
    ("scope01", "scope", "대표자: [[person|A|가상민]]\n사업장 주소: [[address|-|주소값1]]\n대표자 연락처: [[phone_number|A|전화값1]]"),
    ("scope02", "scope", "담당자: [[person|A|가상민]], 회사 대표번호: [[phone_number|-|전화값1]]"),
    ("scope03", "scope", "신청인: [[person|A|가상민]]\n출금 계좌: [[account_number|-|계좌값1]]"),
    ("scope04", "scope", "예금주: [[person|A|가상민]] [[account_number|A|계좌값1]]"),
    ("scope05", "scope", "예금주: [[person|A|가상민]]\n계좌번호: [[account_number|A|계좌값1]]"),
    ("scope06", "scope", "예금주: [[person|A|가상민]] [[account_number|A|계좌값1]], 수수료 수납계좌: [[account_number|-|계좌값2]]"),
    ("scope07", "scope", "가맹점 대표자: [[person|A|가상민]]\n정산 계좌: [[account_number|-|계좌값1]]\n사업장 주소: [[address|-|주소값1]]"),
    ("scope08", "scope", "고객: [[person|A|가상민]]\n고객센터 연락처: [[phone_number|-|전화값1]]\n고객 본인 연락처: [[phone_number|A|전화값2]]"),
    ("lang01", "language", "직원 [[person|A|가상민]]이 고객 [[person|B|가상윤]]의 휴대전화 번호 [[phone_number|B|전화값1]]을 확인했다."),
    ("lang02", "language", "[[person|A|가상민]]의 연락처로 [[phone_number|-|전화값1]]을 기재했으나 본인 번호가 아닌 회사 공용 번호로 확인됨."),
    ("lang03", "language", "[[person|A|가상민]]의 연락처는 미확인이다. 문의는 [[phone_number|-|전화값1]]로 한다. 해당 번호는 공용 안내번호다."),
    ("lang04", "language", "[[person|A|가상민]]의 주소는 [[address|A|주소값1]]이며, [[person|B|가상윤]]의 주소는 [[address|B|주소값2]]이다."),
    ("lang05", "language", "[[person|A|가상민]] 또는 [[person|B|가상윤]] 중 한 명의 이메일 [[email|?|메일값1]]이지만 누구인지는 확인되지 않았다."),
    ("lang06", "language", "[[person|A|가상민]]의 전화번호: [[phone_number|A|전화값1]]\n[[person|B|가상윤]]은 해당 번호의 소유자가 아니다."),
    ("dialog01", "speaker", "상담원: [[person|A|가상민]]\n고객: [[person|B|가상윤]]\n고객: 본인 연락처는 [[phone_number|B|전화값1]]입니다."),
    ("dialog02", "speaker", "상담원: [[person|A|가상민]]\n고객: [[person|B|가상윤]]\n고객: 제 가족 연락처는 [[phone_number|?|전화값1]]입니다. 제 번호는 아닙니다."),
    ("dialog03", "speaker", "상담원: [[person|A|가상민]]\n고객: [[person|B|가상민]]\n고객: 본인 연락처는 [[phone_number|B|전화값1]]입니다."),
    ("dialog04", "speaker", "고객: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값1]]\n고객: [[person|B|가상민]]\n연락처: [[phone_number|B|전화값2]]\n서로 다른 두 고객의 상담 기록."),
    ("dialog05", "speaker", "고객: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값1]]\n고객: [[person|A|가상민]]\n연락처: [[phone_number|A|전화값2]]\n같은 고객이 변경된 번호를 제공함."),
)
TAG=re.compile(r"\[\[([^|]+)\|([^|]+)\|([^\]]+)\]\]")
def decode(source):
    text="";records=[];owners={};ref={};pending=[];pos=0
    for m in TAG.finditer(source):
        text+=source[pos:m.start()]
        lab,pid,value=m.groups();a=len(text);text+=value;b=len(text);pos=m.end()
        # Synthetic tokens preserve exact value equality; no real PII or production normalization.
        token=lab+"_"+hashlib.sha256((lab+"\0"+value).encode()).hexdigest()[:24]
        e=dict(label=lab,start=a,end=b,token=token,normalized=value)
        records.append(e)
        if lab=="person":
            require(pid not in ("-","?"),"PERSON_FIXTURE");owners[span(e)]=pid
        else:pending.append((e,pid))
    text+=source[pos:]
    require("[[" not in text and "]]" not in text and bool(owners) and bool(pending),"FIXTURE_PARSE")
    for e,pid in pending:
        ms=[list(s) for s,p in owners.items() if p==pid]
        st=EVENT if pid=="-" else UNCERTAIN if pid=="?" else PERSON
        require((st==PERSON and bool(ms)) or (st!=PERSON and not ms),"FIXTURE_REFERENCE")
        ref[key(e)]=dict(status=st,owner_mentions=ms)
    return text,records,owners,ref

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args()
    own=team_source(a.fetch_team)
    totals=defaultdict(Counter);families=defaultdict(lambda:defaultdict(Counter));failures=defaultdict(list)
    ref_counts=Counter();mixed=0;held=0
    require(len({f[0] for f in FIXTURES})==len(FIXTURES),"DUPLICATE_CASE")
    for cid,family,source in FIXTURES:
        text,records,truth,ref=decode(source)
        ref_counts.update(x["status"] for x in ref.values())
        subjects,_=own.group_records(text,records)
        found=[span(e) for s in subjects for e in s["checks"] if e["label"]=="person"]
        require(len(found)==len(set(found))==len(truth) and set(found)==set(truth),"MENTION_PARITY")
        n_mixed=sum(len({truth[span(e)] for e in s["checks"] if e["label"]=="person"})>1 for s in subjects)
        n_held=sum(bool(s["force_hold"]) for s in subjects)
        mixed+=n_mixed;held+=n_held
        if n_mixed or n_held:emit("CHALLENGE_SUBJECT",case=cid,mixed_subjects=n_mixed,held_subjects=n_held)
        modes=predict(text,records,subjects)
        for mode,pred in modes.items():
            require(set(pred)==set(ref),"PREDICTION_COVERAGE")
            c,_,rs=grade(pred,ref,subjects,truth)
            totals[mode].update(c);families[mode][family].update(c)
            bad=c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"]
            if bad or c["missed_person"]:
                failures[mode].append(cid)
                emit("CHALLENGE_ERROR",case=cid,family=family,mode=mode,
                     counts=dict(c),reasons={r+":"+o:v for (r,o),v in rs.items()})
    emit("CHALLENGE_INPUT",documents=len(FIXTURES),attributes=sum(ref_counts.values()),reference=dict(ref_counts),
         fixture_sha256=hashlib.sha256(json.dumps(FIXTURES,ensure_ascii=False,separators=(",",":")).encode()).hexdigest(),
         mixed_gold_subjects=mixed,held_subjects=held)
    for mode,c in totals.items():
        tp=c["correct_person"];unsafe=c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"]
        assigned=tp+unsafe;gold=ref_counts[PERSON]
        require(sum(c.values())==sum(ref_counts.values()) and gold==tp+c["wrong_person"]+c["missed_person"],"COUNT_PARITY")
        emit("CHALLENGE_RESULT",mode=mode,assigned=assigned,unsafe_assignments=unsafe,
            precision=tp/assigned if assigned else None,recall=tp/gold,f1=2*tp/(assigned+gold),
            counts=dict(c),by_family={f:dict(x) for f,x in families[mode].items()},error_cases=failures[mode])
    emit("CHALLENGE_COMPLETE",scope="synthetic author-labelled stress test of fixed O0/O1/O2 with perfect spans; not independent human Gold, production performance or Holdout2")
if __name__=="__main__":main()
