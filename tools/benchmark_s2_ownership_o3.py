"""Mention-first attribute association ablation, not production ownership.
O3A isolates premature grouping; O3B uses bounded explicit evidence;
O3C adds guarded weak candidates for comparison ONLY, not identity anchors.
Frozen V7 cache + v0.1 reference, frozen 43-case regression. No DB/LLM.
"""
import argparse, json, re, secrets, time
from collections import Counter, defaultdict
from pathlib import Path
from tools.benchmark_s2_ownership import (
    predict, grade, key, span, PERSON, EVENT, UNCERTAIN, REF_SHA,
    ORG_FIELD, SHARED_FIELD, POSSESSIVE, HOLDER, ADDR_FIELD, RECIPIENT,
)
from tools.benchmark_s2_ownership_challenge import FIXTURES, decode
from tools.benchmark_s2_identity_graph import team_source, require, emit, sha, metrics, pair_predictions
from tools.benchmark_s2_identity_incremental import full_snapshot
from tools.benchmark_step1 import load_data
from starter.s2_pseudonymizer import make_hmac_token,V7_POLICY,READY
from starter.v7_model_store import read_manifest

# Explicit field categories; unknown fields break inheritance.
ROLE=r"(?:대상\s*고객|피해\s*고객|고객(?:명)?|신청인|신청자(?:\s*성명)?|성명|위임인|위임자|대리인|보증인|대표자|수신인|수취인|송금인|예금주|계좌\s*명의인|담당자|담당|직원|상담사|상담원)"
PERSON_HEADER=re.compile(r"^\s*(?:[-·•]|\d+[.)])?\s*("+ROLE+r")\s*[:：]\s*$")
FIELDS={
 "phone_number":r"(?:본인\s*)?(?:휴대전화(?:\s*번호)?|휴대폰(?:\s*번호)?|전화번호|연락처|전화)",
 "resident_registration_number":r"(?:주민등록번호|주민번호)",
 "email":r"(?:이메일|e-?mail|메일)",
 "address":r"(?:(?:반송|변경|청구지|청구서|배송|거주|우편|이전|신규)\s*)?(?:주소|배송지|거주지)",
 "card_number":r"(?:(?:발급|신규)\s*)?카드번호",
 "account_number":r"계좌번호",
}
FIELD_ANY="(?:"+"|".join(FIELDS.values())+")"
FIELD_LINE=re.compile(r"^\s*(?:[-·•]\s*)?(?:(?:"+ROLE+r")\s*)?"+FIELD_ANY+r"\s*[:：]",re.I)
NONPERSON=re.compile(r"공용|공동\s*(?:메일|번호|계좌)|대표\s*(?:번호|전화)|(?:고객|콜)\s*센터\s*(?:연락처|번호|전화)|(?:회사|법인|사업장|가맹점|기관|부서)\s*(?:정보|주소|연락처|전화번호|이메일)|수수료\s*수납계좌|shared\s+(?:helpdesk|mailbox|number)|team\s+mailbox",re.I)
UNCLEAR=re.compile(r"미확인|확인\s*불가|불명확|누구인지|별도\s*지정|가족\s*(?:대리|연락처)|다른\s*(?:사람|인물)의|본인\s*(?:번호|연락처)가\s*아닌|제\s*번호는\s*아닙니다|개별.*(?:미확인|not identified)|\bor\b.*\b(?:by|via)\s+e-?mail\b",re.I)
QUOTE=re.compile(r"전달\s*원문|인용문|quoted|forwarded message|original message|^\s*>",re.I|re.M)
NEG=re.compile(r"아닌|아니다|아닙니다|않(?:음|았다|는|습니다)|\b(?:not|do not|don't)\b",re.I)
BREAK=re.compile(r"[.!?;]\s+|[.!?;]$")
ROLE_ONLY=re.compile(r"^\s*("+ROLE+r")\s*$")
ROLE_PREFIX=re.compile(r"^\s*("+ROLE+r")\s*")
MAIL_CONTEXT=re.compile(r"^\s*(?:To|받는\s*사람|수신자|Subject|제목)\s*[:：]|^\s*Dear\b",re.I|re.M)

def line_context(text,records):
    lines=[];p=0
    for l in text.splitlines(keepends=True):
        lines.append((p,p+len(l),l));p+=len(l)
    row=defaultdict(list)
    for e in records:
        i=next(i for i,(a,b,_) in enumerate(lines) if a<=e["start"]<b)
        row[i].append(e)
    return lines,row

def one(xs):
    vals=set(xs);return next(iter(vals)) if len(vals)==1 else None

def resolve(text,records,ownership):
    legacy,_=ownership.group_records(text,records)
    old=predict(text,records,legacy)
    names=sorted((e for e in records if e["label"]=="person"),key=lambda e:e["start"])
    subjects=[{"checks":[e],"role":"역할 미확인","force_hold":None} for e in names]
    idx={span(e):i for i,e in enumerate(names)}
    lines,row=line_context(text,records)
    where={key(e):i for i,rs in row.items() for e in rs}
    ns=lambda i:[e for e in row[i] if e["label"]=="person"]
    ats=lambda i:[e for e in row[i] if e["label"]!="person"]
    for e,s in zip(names,subjects):
        i=where[key(e)];a,b,_=lines[i];s["role"]=ownership.role_for(text,e,a,b)
    def header(i):
        if len(ns(i))!=1:return None
        n=ns(i)[0];a,b,_=lines[i]
        m=PERSON_HEADER.fullmatch(text[a:n["start"]])
        # Header must contain just the name (or a short honorific), not prose.
        if m and re.fullmatch(r"\s*(?:님|씨)?\s*",text[n["end"]:b]):
            return idx[span(n)],re.sub(r"\s+","",m[1])
        return None
    def block(i):
        # A contiguous run of recognized fields. Unknown headers, blank lines,
        # prose, or another named mention stop inheritance.
        for j in range(i-1,-1,-1):
            h=header(j)
            if h is not None:return h
            if ns(j) or not FIELD_LINE.match(lines[j][2]):break
            if NONPERSON.search(lines[j][2]) or UNCLEAR.search(lines[j][2]):break
        return None
    def local_segment(i,e):
        a,b,l=lines[i]
        # Mask other detected values with spaces so punctuation in values
        # cannot create artificial sentence boundaries.
        chars=list(l)
        for v in row[i]:
            for z in range(v["start"]-a,min(v["end"]-a,len(chars))):chars[z]=" "
        marks=list(BREAK.finditer("".join(chars)))
        start=max([a]+[a+m.end() for m in marks if a+m.end()<=e["start"]])
        end=min([b]+[a+m.start() for m in marks if a+m.start()>=e["end"]])
        return start,end
    def table(i,e):
        a,b,l=lines[i]
        if "|" not in l:return False,None
        def cells(s):
            bars=[-1]+[m.start() for m in re.finditer(r"\|",s)]+[len(s)]
            return [(x+1,y,s[x+1:y].strip()) for x,y in zip(bars,bars[1:])]
        cs=cells(l);col=next((j for j,(x,y,_) in enumerate(cs) if x<=e["start"]-a<y),None)
        if col is None:return True,None
        # Search contiguous table for a non-data header of equal arity.
        hdr=None
        for j in range(i-1,-1,-1):
            if "|" not in lines[j][2]:break
            h=cells(lines[j][2])
            if len(h)!=len(cs):break
            if not row[j] and any(ROLE_ONLY.fullmatch(z) for _,_,z in h):
                hdr=h;break
        if hdr is None:
            # No column metadata: do not treat proximity as a strong relation.
            return True,None
        lab=hdr[col][2]
        if NONPERSON.search(lab) or not re.search(FIELDS[e["label"]],lab,re.I):return True,None
        target=ROLE_PREFIX.match(lab)
        candidates=[]
        for n in ns(i):
            nc=next((k for k,(x,y,_) in enumerate(cs) if x<=n["start"]-a<y),None)
            if nc is None:continue
            role=ROLE_ONLY.fullmatch(hdr[nc][2])
            if role and (not target or re.sub(r"\s+","",target[1])==re.sub(r"\s+","",role[1])):
                candidates.append(idx[span(n)])
        return True,one(candidates)
    def map_old(k,i,e):
        j,reason=old["O2_structure"][k]
        if j is None:return None,reason
        members=[n for n in legacy[j]["checks"] if n["label"]=="person"]
        if len(members)==1:return idx[span(members[0])],reason
        local=[idx[span(n)] for n in members if where[key(n)]==i]
        if len(local)==1:return local[0],reason
        h=block(i)
        if h is not None and h[0] in {idx[span(n)] for n in members}:return h[0],reason
        return None,"ambiguous_legacy_mention"

    out={m:{} for m in ("O3A_occurrence_only","O3B_explicit_scope","O3C_candidate_union")}
    for e in records:
        if e["label"]=="person":continue
        k=key(e);i=where[k];a,b,line=lines[i];lab=e["label"]
        inherited,oldreason=map_old(k,i,e)
        out["O3A_occurrence_only"][k]=(inherited,oldreason)
        lo,hi=local_segment(i,e);segment=text[lo:hi]
        # Whole-line guards intentionally conservative; do not use Gold.
        blocked=None
        if ORG_FIELD.match(line) or SHARED_FIELD.match(line) or NONPERSON.search(line):blocked="nonperson_scope"
        elif UNCLEAR.search(line) or NEG.search(segment):blocked="ambiguous_or_negated"
        elif QUOTE.search(line):blocked="quoted_scope"
        h=block(i)
        # Unknown intervening fields cannot be crossed by fallback.
        if not blocked and i>0 and (UNCLEAR.search(lines[i-1][2]) or NONPERSON.search(lines[i-1][2])) and not ns(i):
            blocked="prior_scope_change"
        # Recipient/signoff association is only tentative; reject explicit mail
        # ambiguity, multiple addresses, shared mailbox, forwarding and negation.
        if not blocked and oldreason in ("header_to_salutation","self_contact_signoff"):
            if QUOTE.search(text) or NONPERSON.search(text) or UNCLEAR.search(text):
                blocked="mail_scope_conflict"
            elif oldreason=="header_to_salutation" and len([v for v in ats(i) if v["label"]=="email"])!=1:
                blocked="multiple_recipients"
        proposals=[]
        is_table,table_owner=table(i,e)
        if not blocked:
            if is_table:
                if table_owner is not None:proposals.append((table_owner,"table_column"))
            else:
                # Explicit possessive limited to one sentence and one attribute
                # segment; no crossing another mention or earlier attribute.
                for n in ns(i):
                    if not lo<=n["start"]<n["end"]<=e["start"]:continue
                    gap=text[n["end"]:e["start"]]
                    if len(gap)>80 or any(n["end"]<=v["start"]<e["start"] for v in row[i] if v is not n and v is not e):continue
                    if lab!="account_number" and POSSESSIVE.match(gap) and re.search(FIELDS[lab],gap,re.I):
                        proposals.append((idx[span(n)],"bounded_possessive"))
                    if lab=="account_number" and HOLDER.search(text[lo:n["start"]]):
                        if re.fullmatch(r"[\s:：()（）-]*",gap):
                            proposals.append((idx[span(n)],"explicit_holder"))
                # Named field block (no document-wide role fallback).
                if not ns(i) and h is not None:
                    who,role=h
                    field=re.match(r"^\s*(?:[-·•]\s*)?(?:(?:"+ROLE+r")\s*)?"+FIELDS[lab]+r"\s*[:：]",line,re.I)
                    if field:
                        prefix=ROLE_PREFIX.match(line)
                        role_ok=not prefix or re.sub(r"\s+","",prefix[1])==role
                        if role_ok and (lab!="account_number" or role in ("예금주","계좌명의인")):
                            proposals.append((who,"explicit_field_block"))
                # Explicit named role and adjacent labelled/parenthesized value.
                local_names=[n for n in ns(i) if lo<=n["start"]<hi]
                if len(local_names)==1 and lab!="account_number":
                    n=local_names[0]
                    if n["end"]<=e["start"]:
                        gap=text[n["end"]:e["start"]]
                        pre=text[lo:n["start"]]
                        if not any(n["end"]<=v["start"]<e["start"] for v in row[i] if v is not n and v is not e):
                            explicit=bool(PERSON_HEADER.fullmatch(pre)) and bool(re.search(FIELDS[lab],gap,re.I) or re.fullmatch(r"\s*\(",gap))
                            if explicit:proposals.append((idx[span(n)],"named_inline_field"))
                # Speaker turn: nearest preceding explicit speaker of the same
                # role, stopping at another explicit mention of that role.
                speaker=re.match(r"^\s*(고객|신청인|신청자)\s*[:：]\s*(?:본인\s*)?(?:연락처는|전화번호는|휴대전화\s*번호는)?\s*$",text[a:e["start"]])
                if speaker and lab=="phone_number" and not ns(i):
                    for j in range(i-1,-1,-1):
                        if not lines[j][2].strip():break
                        hh=header(j)
                        if hh and hh[1]==speaker[1]:
                            proposals.append((hh[0],"speaker_turn"));break
                        if re.match(r"^\s*"+re.escape(speaker[1])+r"\s*[:：]",lines[j][2]) and ns(j):break
        chosen=one(x[0] for x in proposals)
        if blocked:strong=(None,blocked)
        elif chosen is not None:
            # Independent explicit proposals must agree; legacy is not proof.
            strong=(chosen,"+".join(sorted({r for _,r in proposals})))
        elif proposals:strong=(None,"explicit_conflict")
        else:strong=(None,"no_explicit_scope")
        out["O3B_explicit_scope"][k]=strong
        # Weak candidate comparison, not a confirmed assignment. No recovery
        # through unparsed table columns or explicit conflict.
        if strong[0] is not None or blocked or proposals or is_table:
            weak=strong
        elif inherited is not None:
            # Avoid old arbitrary whole-document fallback for fields.
            n=names[inherited]
            local=where[key(n)]==i and lo<=n["start"]<hi
            permitted_mail=oldreason in ("header_to_salutation","self_contact_signoff")
            if local or permitted_mail:
                weak=(inherited,"tentative:"+oldreason)
            else:weak=(None,"unscoped_legacy")
        else:weak=strong
        out["O3C_candidate_union"][k]=weak
    return subjects,out,legacy,old

def score_rows(rows,own,scope):
    counts=defaultdict(Counter);reasons=defaultdict(Counter);families=defaultdict(lambda:defaultdict(Counter))
    all_nodes=defaultdict(list);global_truth={};refs=Counter();times=[];mixed=Counter()
    for did,family,text,records,truth,ref in rows:
        refs.update(v["status"] for v in ref.values())
        t=time.perf_counter();subjects,new,legacy,old=resolve(text,records,own);times.append((time.perf_counter()-t)*1000)
        lookup={key(e):e for e in records}
        for s,preds in ((legacy,old),(subjects,new)):
            require(len([e for sub in s for e in sub["checks"] if e["label"]=="person"])==len(truth),"MENTION_PARITY")
            m=sum(len({truth[span(e)] for e in sub["checks"] if e["label"]=="person"})>1 for sub in s)
            for mode,pred in preds.items():
                require(set(pred)==set(ref),"ATTR_PARITY")
                mixed[mode]+=m
                c,_,rs=grade(pred,ref,s,truth);counts[mode].update(c);reasons[mode].update(rs);families[mode][family].update(c)
                bad=c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"]
                if scope=="regression" and mode.startswith("O3") and (bad or c["missed_person"]):
                    emit("O3_CASE",case=did,family=family,mode=mode,counts=dict(c),reasons={r+":"+o:v for (r,o),v in rs.items()})
                if scope=="A187":
                    by=defaultdict(lambda:defaultdict(set))
                    for k,(i,_) in pred.items():
                        if i is not None:by[i][k[0]].add(lookup[k]["token"])
                    for i,sub in enumerate(s):
                        ns=[e for e in sub["checks"] if e["label"]=="person"]
                        ms=tuple(sorted((did,e["start"],e["end"]) for e in ns))
                        nms={e["normalized"] for e in ns};require(len(nms)==1,"NAME_PARITY")
                        all_nodes[mode].append(dict(id=ms,doc=did,name=next(iter(nms)),role=sub["role"],attrs=dict(by[i]),held=bool(sub["force_hold"]),mentions=ms))
        global_truth.update({(did,*s):pid for s,pid in truth.items()})
    emit("O3_INPUT",scope=scope,documents=len(rows),reference=dict(refs),mean_all_modes_ms=sum(times)/len(times))
    for mode,c in counts.items():
        tp=c["correct_person"];bad=c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"];assigned=tp+bad;g=refs[PERSON]
        require(sum(c.values())==sum(refs.values()) and tp+c["wrong_person"]+c["missed_person"]==g,"SCORE_PARITY")
        emit("O3_RESULT",scope=scope,mode=mode,counts=dict(c),unsafe=bad,assigned=assigned,
             precision=tp/assigned if assigned else None,recall=tp/g,f1=2*tp/(g+assigned),mixed_subjects=mixed[mode],
             reasons={r+":"+o:v for (r,o),v in sorted(reasons[mode].items())})
        # Only explicit-scope and prior baselines are compared in graph; weak
        # candidates must not be used as identity anchors.
        if scope=="A187" and mode!="O3C_candidate_union" and mode!="O3A_occurrence_only":
            ns=all_nodes[mode];ix={n["id"]:i for i,n in enumerate(ns)}
            pairs={tuple(sorted((ix[x],ix[y]))) for x,y in full_snapshot(ns)}
            emit("O3_GRAPH",mode=mode,pair_metrics=metrics(global_truth,pair_predictions(ns,pairs,True)),scope="experimental association anchors only")

def corpus(cache,reference):
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval";dp=root/"holdout_labeled.jsonl";gp=root/"holdout_persons_gold.jsonl"
    require(sha(reference)==REF_SHA,"REFERENCE_HASH")
    ref=json.loads(reference.read_text());require(ref["document_sha256"]==sha(dp) and ref["person_gold_sha256"]==sha(gp),"REFERENCE_SOURCE")
    refs={r["doc_id"]:{key(e):e for e in r["attributes"]} for r in ref["rows"]}
    pg={str(r["id"]):r["persons"] for r in (json.loads(l) for l in gp.read_text().splitlines() if l.strip())}
    dc=json.loads(cache.read_text());m=read_manifest(Path.cwd()/"models/v7_model_release.json")
    require(dc["document_sha256"]==sha(dp) and dc["policy"]==V7_POLICY and dc["model_revision"]==m["revision"],"CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]}
    docs=load_data("A187",dp);ids={str(d["id"]) for d in docs}
    require(len(ids)==len(refs)==len(pg)==len(dm)==187 and ids==set(refs)==set(pg)==set(dm),"DOC_IDS")
    rows=[];secret=secrets.token_bytes(32)
    for d in docs:
        did=str(d["id"]);text=d["text"];r=dm[did];g=refs[did]
        if r["decision"]!=READY:continue
        require(r["length"]==len(text),"DOC_LENGTH")
        truth={span(e):e["person_id"] for e in pg[did]};records=[]
        for e in r["entities"]:
            a,b,lab=e["start"],e["end"],e["label"];require(0<=a<b<=len(text),"OFFSETS")
            token,norm=make_hmac_token(secret,lab,text[a:b]);records.append(dict(label=lab,start=a,end=b,token=token,normalized=norm))
        require({key(e) for e in records if e["label"]!="person"}==set(g) and {span(e) for e in records if e["label"]=="person"}==set(truth),"V7_PARITY")
        rows.append((did,"A187",text,records,truth,g))
    require(len(rows)==184 and sum(len(r[4]) for r in rows)==551 and sum(len(r[5]) for r in rows)==340,"CORPUS_PARITY")
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--fetch-team",action="store_true")
    p.add_argument("--document-cache",type=Path,required=True);p.add_argument("--reference",type=Path,required=True)
    a=p.parse_args();own=team_source(a.fetch_team)
    rows=[(cid,family,*decode(src)) for cid,family,src in FIXTURES]
    require(len(rows)==43 and sum(len(r[5]) for r in rows)==57,"REGRESSION_PARITY")
    score_rows(rows,own,"regression")
    score_rows(corpus(a.document_cache.expanduser(),a.reference.expanduser()),own,"A187")
    emit("O3_COMPLETE",scope="development ablation; no production changes, no Holdout2; tentative links not authorized")
if __name__=="__main__":main()
