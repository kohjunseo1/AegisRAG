"""Evaluate document-local attribute association, not legal ownership.
Reference annotations are evaluation only. Frozen V7 cache; no model/DB/LLM.
O0 pinned team, O1 conservative nonperson/ambiguous guards, O2 adds structural evidence.
Development experiment, not a production identity or disclosure authorization.
"""
import argparse,json,re,secrets
from collections import Counter,defaultdict
from pathlib import Path
from tools.benchmark_s2_identity_graph import team_source,sha,require,emit,metrics,pair_predictions
from tools.benchmark_s2_identity_incremental import full_snapshot
from tools.benchmark_step1 import load_data
from starter.s2_pseudonymizer import make_hmac_token,V7_POLICY,READY
from starter.v7_model_store import read_manifest

PERSON="PERSON_ASSOCIATED"
EVENT="EVENT_OR_ORG"
UNCERTAIN="UNCERTAIN"
REF_SHA="5f13ed960353d7496ce4799927a1e6dc2779f129a0e8ec028505d25ce9fd2150"
ORG_FIELD=re.compile(r"^\s*(?:[-·•]\s*)?(?:사업장|회사|법인|가맹점|본사|지점|기관)\s*(?:주소|연락처|전화번호|이메일)\s*[:：]")
SHARED_FIELD=re.compile(r"^\s*(?:[-·•]\s*)?(?:대표\s*(?:전화|번호)|(?:고객|콜)\s*센터\s*(?:전화|번호|연락처))\s*[:：]")
HOLDER=re.compile(r"(?:예금주|계좌\s*명의인|계좌\s*소유자)\s*[:：]?\s*$")
# Bounded, explicit field syntax only; never infer from doc_type or name strings.
ADDR_FIELD=re.compile(r"^\s*(?:[-·•]\s*)?(?:(?:반송|변경|청구지|청구서|배송|거주|우편|이전|신규)\s*)?(?:주소|배송지|거주지)\s*[:：]")
SUBJECT_FIELD=re.compile(r"^\s*(?:[-·•]\s*)?(?:대상\s*고객|고객명?|신청인|신청자|성명|수신인|명의인|수취인|송금인)\s*[:：]")
CONT_FIELD=re.compile(r"^\s*(?:[-·•]\s*)?[^:\n：]{1,24}[:：]")
RECIPIENT=re.compile(r"^\s*(?:받는\s*사람|수신자|To)\s*[:：]",re.I)
GREETING=re.compile(r"^\s*(?:Dear\s+)?$")
GREETING_END=re.compile(r"^\s*(?:님[,，]?\s*(?:안녕하세요|귀하)|님[,，]|[,，])",re.I)
SIGNOFF=re.compile(r"^\s*(?:Best regards|Kind regards|Regards|Sincerely|감사합니다)\s*[,，.!]?\s*$",re.I)
SELF_CONTACT=re.compile(r"\b(?:call|reach|contact)\s+me\s+(?:at|on)\b",re.I)
AMBIG_EMAIL=re.compile(r"\bor\b.*\b(?:by|via)\s+e-?mail\b",re.I)
POSSESSIVE=re.compile(r"^\s*(?:님|씨)?\s*의\s+(?:주민등록번호|주민번호|휴대전화|휴대폰|전화번호|연락처|이메일|주소|카드번호)")

def key(e):return (e["label"],e["start"],e["end"])
def span(e):return (e["start"],e["end"])
def uniq(xs):
    xs=set(xs);return next(iter(xs)) if len(xs)==1 else None

def make_context(text,records,subjects):
    lines=[];p=0
    for l in text.splitlines(keepends=True):lines.append((p,p+len(l),l));p+=len(l)
    names=[r for r in records if r["label"]=="person"]
    subject_of={}
    for i,s in enumerate(subjects):
        for e in s["checks"]:
            if e["label"]=="person":
                require(span(e) not in subject_of,"DUPLICATE_NAME_SUBJECT")
                subject_of[span(e)]=i
    line_names=defaultdict(list)
    def line_no(e):
        return next(i for i,(a,b,_) in enumerate(lines) if a<=e["start"]<b)
    for n in names:line_names[line_no(n)].append(n)
    base={}
    for i,s in enumerate(subjects):
        for e in s["checks"]:
            if e["label"]=="person":continue
            require(key(e) not in base,"DUPLICATE_OWNER")
            base[key(e)]=i
    return lines,line_names,subject_of,base,line_no

def predict(text,records,subjects):
    lines,ln,so,base,line_no=make_context(text,records,subjects)
    def only(ns):return uniq(so[span(n)] for n in ns)
    def explicit_holder(i,e):
        a,_,_=lines[i]
        ns=[n for n in ln[i] if n["end"]<=e["start"] and HOLDER.search(text[a:n["start"]])]
        return only(ns)
    def block_owner(i):
        # nearest explicit subject header in same contiguous field block.
        for j in range(i-1,max(-1,i-7),-1):
            a,b,l=lines[j]
            if not l.strip():break
            ns=ln[j]
            if ns:
                if SUBJECT_FIELD.match(l) and len(ns)==1:return only(ns)
                break
            if not CONT_FIELD.match(l):break
        return None
    def recipient(i,e):
        if not RECIPIENT.match(lines[i][2]) or ln[i]:return None
        # Require one explicit salutation before any other person mention.
        for j in range(i+1,min(len(lines),i+7)):
            ns=ln[j]
            if not ns:continue
            if len(ns)!=1:return None
            n=ns[0];a,b,_=lines[j]
            if GREETING.fullmatch(text[a:n["start"]]) and GREETING_END.match(text[n["end"]:b]):
                return only(ns)
            return None
        return None
    def sender(i):
        # Explicit first-person contact + unique sign-off followed by a name.
        hits=[]
        for j in range(i+1,min(len(lines),i+9)):
            if SIGNOFF.fullmatch(lines[j][2].strip()):
                k=j+1
                if k<len(lines) and len(ln[k])==1:
                    n=ln[k][0];a,b,_=lines[k]
                    if not text[a:n["start"]].strip() and not text[n["end"]:b].strip():hits.append(so[span(n)])
        return uniq(hits)
    results={m:{} for m in ("O0_team","O1_guard","O2_structure")}
    for e in records:
        if e["label"]=="person":continue
        k=key(e);i=line_no(e);a,b,line=lines[i];lab=e["label"]
        old=base.get(k);guarded=old;reason="team_assignment" if old is not None else "unassigned"
        results["O0_team"][k]=(old,reason)
        # Explicit scope exclusions apply to person ownership, not masking or
        # exact account/address retrieval; these values remain indexed.
        holder=explicit_holder(i,e) if lab=="account_number" else None
        blocked=False
        if lab=="account_number" and holder is None:
            guarded=None;reason="account_holder_not_explicit";blocked=True
        elif ORG_FIELD.match(line) or SHARED_FIELD.match(line):
            guarded=None;reason="org_or_shared_field";blocked=True
        elif lab=="email" and AMBIG_EMAIL.search(line):
            guarded=None;reason="ambiguous_email_recipient";blocked=True
        results["O1_guard"][k]=(guarded,reason)
        new=guarded;why=reason
        if not blocked:
            proposals=[]
            if holder is not None:proposals.append((holder,"explicit_account_holder"))
            if lab=="address" and ADDR_FIELD.match(line) and not ln[i]:
                owner=block_owner(i)
                if owner is not None:proposals.append((owner,"bounded_address_field"))
            if lab=="email":
                owner=recipient(i,e)
                if owner is not None:proposals.append((owner,"header_to_salutation"))
            if lab=="phone_number" and SELF_CONTACT.search(text[a:e["start"]]):
                owner=sender(i)
                if owner is not None:proposals.append((owner,"self_contact_signoff"))
            for n in ln[i]:
                if n["end"]<=e["start"]:
                    gap=text[n["end"]:e["start"]]
                    if len(gap)<=80 and POSSESSIVE.match(gap):
                        # Reject an intervening other named mention.
                        if not any(n["end"]<=other["start"]<e["start"] and other is not n for other in ln[i]):
                            proposals.append((so[span(n)],"explicit_possessive"))
            if proposals:
                chosen=uniq(x[0] for x in proposals)
                if chosen is None:new=None;why="structural_conflict"
                else:new=chosen;why=next(r for x,r in proposals if x==chosen)
        results["O2_structure"][k]=(new,why)
    return results

def grade(pred,ref,subjects,person_ids):
    c=Counter();types=defaultdict(Counter);reasons=Counter()
    for k,(i,why) in pred.items():
        g=ref[k];st=g["status"];assigned=i is not None
        if st==PERSON:
            want={person_ids[tuple(m)] for m in g["owner_mentions"]}
            require(len(want)==1,"REFERENCE_OWNER")
            got={person_ids[span(n)] for n in subjects[i]["checks"] if n["label"]=="person"} if assigned else set()
            outcome="correct_person" if got==want else "wrong_person" if assigned else "missed_person"
        else:outcome=("unsafe_scope_assignment" if assigned else "scope_abstained") if st==EVENT else ("uncertain_assigned" if assigned else "uncertain_abstained")
        c[outcome]+=1;types[k[0]][outcome]+=1;reasons[why,outcome]+=1
    return c,types,reasons

def self_test():
    # Fixtures use artificial placeholders and a supplied team assignment.
    def run(text,ns,attr,base):
        records=[dict(label="person",start=text.index(n),end=text.index(n)+len(n)) for n in ns]
        lab,value=attr;a=text.index(value);e=dict(label=lab,start=a,end=a+len(value));records.append(e)
        subjects=[dict(checks=[r]+([e] if i==base else [])) for i,r in enumerate(records[:-1])]
        return predict(text,records,subjects)["O2_structure"][key(e)][0]
    require(run("대표자: 가상민\n사업장 주소: 주소값",["가상민"],("address","주소값"),0) is None,"ORG_ADDRESS")
    require(run("신청인: 가상민\n출금 계좌: 계좌값",["가상민"],("account_number","계좌값"),0) is None,"ACCOUNT_SCOPE")
    require(run("예금주: 가상민 계좌값",["가상민"],("account_number","계좌값"),None)==0,"EXPLICIT_HOLDER")
    require(run("대상 고객: 가상민\n카드번호: 카드값\n변경 주소: 주소값",["가상민"],("address","주소값"),None)==0,"BOUNDED_FIELD")
    require(run("대상 고객: 가상민\n\n변경 주소: 주소값",["가상민"],("address","주소값"),None) is None,"BLOCK_BREAK")
    require(run("받는 사람: 메일값\n제목: 안내\n\n가상민 님, 안녕하세요.",["가상민"],("email","메일값"),None)==0,"RECIPIENT")
    require(run("Dear 가상민,\ncall me at 번호값.\nBest regards,\n가상윤",["가상민","가상윤"],("phone_number","번호값"),None)==1,"SIGNER")
    require(run("Dear 가상민,\nshare with me or with another by email 메일값.\nBest regards,\n가상윤",["가상민","가상윤"],("email","메일값"),0) is None,"AMBIG_EMAIL")
    emit("OWNERSHIP_SELF_TEST",passed=8)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--document-cache",type=Path,required=True);p.add_argument("--reference",type=Path,required=True);p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args();self_test()
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval";dp=root/"holdout_labeled.jsonl";gp=root/"holdout_persons_gold.jsonl"
    docs=load_data("A187",dp);refp=a.reference.expanduser()
    require(sha(refp)==REF_SHA,"REFERENCE_HASH")
    ref=json.loads(refp.read_text());require(ref["document_sha256"]==sha(dp) and ref["person_gold_sha256"]==sha(gp),"REFERENCE_SOURCE")
    refs={r["doc_id"]:{key(e):e for e in r["attributes"]} for r in ref["rows"]}
    pg={str(r["id"]):r["persons"] for r in (json.loads(l) for l in gp.read_text().splitlines() if l.strip())}
    dc=json.loads(a.document_cache.expanduser().read_text());m=read_manifest(Path.cwd()/"models/v7_model_release.json")
    require(dc["document_sha256"]==sha(dp) and dc["policy"]==V7_POLICY and dc["model_revision"]==m["revision"],"CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]};ids={str(d["id"]) for d in docs}
    require(len(ids)==len(refs)==len(ref["rows"])==len(pg)==len(dm)==len(dc["private_document_offsets"])==187 and ids==set(refs)==set(pg)==set(dm),"DOC_IDS")
    own=team_source(a.fetch_team);secret=secrets.token_bytes(32)
    totals=defaultdict(Counter);types=defaultdict(lambda:defaultdict(Counter));reasons=defaultdict(Counter);nodes=defaultdict(list);truth={};status_counts=Counter();nready=0
    for d in docs:
        did=str(d["id"]);text=d["text"];r=dm[did];g=refs[did]
        require(set(g)=={key(e) for e in d["entities"] if e["label"]!="person"},"REFERENCE_SPANS")
        if r["decision"]!=READY:continue
        nready+=1;require(r["length"]==len(text),"DOC_LENGTH")
        pid={span(e):e["person_id"] for e in pg[did]}
        records=[]
        for e in r["entities"]:
            x,y,lab=e["start"],e["end"],e["label"]
            require(type(x) is int and type(y) is int and 0<=x<y<=len(text),"OFFSETS")
            token,norm=make_hmac_token(secret,lab,text[x:y]);records.append(dict(label=lab,start=x,end=y,token=token,normalized=norm))
        require({key(e) for e in records if e["label"]!="person"}==set(g) and {span(e) for e in records if e["label"]=="person"}==set(pid),"V7_PARITY")
        for e in records:
            if e["label"]=="person":truth[did,e["start"],e["end"]]=pid[span(e)]
        status_counts.update(x["status"] for x in g.values())
        subjects,_=own.group_records(text,records);pred=predict(text,records,subjects);lookup={key(e):e for e in records}
        for mode,ps in pred.items():
            c,t,rs=grade(ps,g,subjects,pid);totals[mode].update(c);reasons[mode].update(rs)
            for lab,ct in t.items():types[mode][lab].update(ct)
            by_subject=defaultdict(lambda:defaultdict(set))
            for k,(i,_) in ps.items():
                if i is not None:by_subject[i][k[0]].add(lookup[k]["token"])
            for i,s in enumerate(subjects):
                ns=[e for e in s["checks"] if e["label"]=="person"];names={e["normalized"] for e in ns};require(len(names)==1,"SUBJECT_NAME")
                ms=tuple(sorted((did,e["start"],e["end"]) for e in ns))
                nodes[mode].append(dict(id=ms,doc=did,name=next(iter(names)),role=s["role"],attrs=dict(by_subject[i]),held=bool(s["force_hold"]),mentions=ms))
    require(nready==184 and len(truth)==551 and status_counts==Counter({PERSON:282,EVENT:50,UNCERTAIN:8}),"READY_REFERENCE_PARITY")
    emit("OWNERSHIP_INPUT",ready_docs=nready,mentions=len(truth),attributes=sum(status_counts.values()),reference=dict(status_counts))
    for mode,c in totals.items():
        good=c["correct_person"];assigned=good+c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"];fn=c["missed_person"]+c["wrong_person"]
        if mode=="O0_team":require(assigned==256,"TEAM_BASELINE_PARITY")
        emit("OWNERSHIP_RESULT",mode=mode,assigned=assigned,**dict(c),precision=good/assigned if assigned else None,
            recall=good/(good+fn),f1=2*good/(assigned+good+fn),by_label={k:dict(v) for k,v in types[mode].items()})
        emit("OWNERSHIP_REASON",mode=mode,counts={r+":"+o:n for (r,o),n in sorted(reasons[mode].items())})
        ns=nodes[mode];idx={n["id"]:i for i,n in enumerate(ns)}
        links=full_snapshot(ns);pairs={tuple(sorted((idx[x],idx[y]))) for x,y in links}
        score=metrics(truth,pair_predictions(ns,pairs,True))
        if mode=="O0_team":require(score["cross_doc"]["tp"]==255 and score["cross_doc"]["fp"]==0,"IDENTITY_PARITY")
        emit("OWNERSHIP_IDENTITY_IMPACT",mode=mode,pair_metrics=score,scope="experimental attribution links, not verified anchors")
    emit("OWNERSHIP_COMPLETE",scope="development-only provisional reference; no legal ownership, egress, DB or Holdout2 evaluation")
if __name__=="__main__":main()
