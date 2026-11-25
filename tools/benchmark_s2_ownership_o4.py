"""O4: scoped relation evidence and identity graph factor ablation.

Prediction sees only text and detected records; no document type, qid, PID,
reference or fixed name. Existing O3B evidence is experimental association,
not verified ownership. O4 candidate relations must not authorize disclosure.
No V7 rerun, DB, external LLM or Holdout2.
"""
import argparse,re,time
from collections import Counter,defaultdict
from pathlib import Path
from tools.benchmark_s2_ownership import grade,key,span,PERSON
from tools.benchmark_s2_ownership_o3 import (
    resolve,corpus,line_context,one,ROLE,FIELDS,NONPERSON,UNCLEAR,QUOTE,NEG,
)
from tools.benchmark_s2_ownership_challenge import FIXTURES,decode
from tools.benchmark_s2_identity_graph import team_source,require,emit,metrics,pair_predictions
from tools.benchmark_s2_identity_incremental import full_snapshot

# Generic field/relationship vocabulary, no document-type switches.
FIELD=re.compile(r"^\s*(?:[-·•]|\d+[.)])?\s*([^:：\n]{1,40})\s*[:：]\s*")
NAME_ROLE=re.compile(r"^\s*(?:님|씨|고객(?:님)?|과장|대리|책임|팀장|부장|직원|상담사|상담원|심사역)?\s*")
TITLE=re.compile(r"^\s*(?:"+ROLE+r")\s*$")
CONTACT=re.compile(r"연락처|연락\s*(?:번호|이메일)|전화|휴대|이메일|e-?mail",re.I)
TRANSACTION=re.compile(r"(?:출금|입금|환급|정산|수납|결제)\s*계좌")
HOLDER_ROLE=re.compile(r"^(?:예금주|계좌\s*명의인|계좌\s*소유자)$")
SCOPE_CHANGE=re.compile(r"위임|대리\s*신청|보증인|수령인|대상자|별도|다른\s*(?:사람|고객)|(?:회사|법인)\s*정보")
ADDRESSEE=re.compile(r"^\s*(?:님|씨)?\s*귀하\s*$")
# Strong separation requires punctuation and explicit subsequent field label.
SEP=re.compile(r"[,;/]\s*(?=[가-힣A-Za-z][^,;/\n]{0,28}[:：])")
ATTRIBUTE_NAMES={k:re.compile(v,re.I) for k,v in FIELDS.items()}

def field_label(line):
    m=FIELD.match(line);return m[1].strip() if m else None

def label_matches(label,lab):
    if not label:return False
    return bool(ATTRIBUTE_NAMES[lab].search(label) or (lab in ("email","phone_number") and CONTACT.fullmatch(label)))

def safe(s):
    return not (NONPERSON.search(s) or UNCLEAR.search(s) or QUOTE.search(s) or NEG.search(s))

def refine(text,records,own):
    subjects,modes,legacy,old=resolve(text,records,own)
    names=[s["checks"][0] for s in subjects]
    ix={span(n):i for i,n in enumerate(names)}
    lines,row=line_context(text,records)
    at={key(e):i for i,rs in row.items() for e in rs}
    ns=lambda i:[e for e in row[i] if e["label"]=="person"]
    strong=modes["O3B_explicit_scope"]
    candidates=modes["O3C_candidate_union"]
    strict={};wide={}
    def header(i):
        if len(ns(i))!=1:return None
        n=ns(i)[0];a,b,l=lines[i];label=field_label(l)
        if not label or not TITLE.fullmatch(label):return None
        # Only one name in the actual field value, optional inline typed values
        # are handled separately, not treated as a new subject.
        m=FIELD.match(l)
        if text[a+m.end():n["start"]].strip():return None
        return ix[span(n)],label
    def block(i,e):
        # Recognized attributes and transaction-only fields preserve the
        # enclosing person scope, but are not personal ownership assertions.
        # Explicit role-qualified fields can return to a known enclosing
        # person after an organization attribute, never across a new person.
        label=field_label(lines[i][2])
        if not label_matches(label,e["label"]) or not safe(lines[i][2]):return None
        if e["label"]=="account_number" and TRANSACTION.search(label):return None
        for j in range(i-1,-1,-1):
            h=header(j)
            if h:
                who,role=h
                if e["label"]=="account_number" and not HOLDER_ROLE.fullmatch(role):return None
                qualifiers=[m.group(0) for m in re.finditer(ROLE,label)]
                if qualifiers and all(re.sub(r"\s+","",q)!=re.sub(r"\s+","",role) for q in qualifiers):return None
                return who
            line=lines[j][2];f=field_label(line)
            if ns(j) or not line.strip() or not f or QUOTE.search(line) or UNCLEAR.search(line) or SCOPE_CHANGE.search(f):break
            if TRANSACTION.search(f):continue
            if NONPERSON.search(f):
                # An explicitly qualified field (e.g. representative contact)
                # may return to the same subject; an unqualified one may not.
                if not re.match(ROLE,label):break
                continue
            if not any(label_matches(f,lab) for lab in FIELDS):break
        return None
    def segment(i,e):
        a,b,l=lines[i];chars=list(l)
        for v in row[i]:
            for z in range(v["start"]-a,min(v["end"]-a,len(chars))):chars[z]=" "
        clean="".join(chars)
        # Split sentence boundaries, or explicit new field, not punctuation
        # inside detected values. Keep negation/correction in same clause.
        marks=list(re.finditer(r"[.!?;]\s+",clean))+list(SEP.finditer(clean))
        lo=max([a]+[a+m.end() for m in marks if a+m.end()<=e["start"]])
        hi=min([b]+[a+m.start() for m in marks if a+m.start()>=e["end"]])
        return lo,hi
    def inline(i,e):
        lo,hi=segment(i,e);s=text[lo:hi]
        if not safe(s) or "|" in s:return None
        people=[n for n in ns(i) if lo<=n["start"]<hi]
        if not people:return None
        lab=e["label"]
        n=max((n for n in people if n["end"]<=e["start"]),key=lambda n:n["end"],default=None)
        if n is None:return None
        gap=text[n["end"]:e["start"]]
        # Other typed attributes are allowed only within one explicit
        # person's field, with labels removed by offsets, never across names.
        prev=[v for v in row[i] if n["end"]<=v["start"] and v["end"]<=e["start"]]
        for v in sorted(prev,key=lambda v:v["start"],reverse=True):
            gap=gap[:v["start"]-n["end"]]+" "+gap[v["end"]-n["end"]:]
        if lab=="account_number":
            if not re.search(r"(?:예금주|계좌\s*명의인|계좌\s*소유자)\s*[:：]?\s*$",text[lo:n["start"]]):return None
            if prev or not re.fullmatch(r"[\s:：()（）-]*",gap):return None
            return ix[span(n)]
        # Explicit field label near the attribute, or immediate parenthetical
        # contact of a single named participant. Avoid arbitrary prose.
        label=field_label(text[lo:hi]);head=bool(label and TITLE.fullmatch(label))
        near=bool(ATTRIBUTE_NAMES[lab].search(gap))
        paren=bool(re.fullmatch(r"\s*\(\s*",NAME_ROLE.sub("",gap,count=1)))
        if len(people)==1 and (head and near or paren and not prev):
            return ix[span(n)]
        if re.match(r"^\s*(?:님|씨)?\s*의\s*",gap) and near and not prev:
            return ix[span(n)]
        return None
    def table(i,e):
        a,b,l=lines[i]
        if "|" not in l:return None
        def cells(s):
            p=[-1]+[m.start() for m in re.finditer(r"\|",s)]+[len(s)]
            return [(x+1,y,s[x+1:y].strip()) for x,y in zip(p,p[1:])]
        cs=cells(l);col=next((j for j,(x,y,_) in enumerate(cs) if x<=e["start"]-a<y),None)
        if col is None or not safe(cs[col][2]):return None
        for j in range(i-1,-1,-1):
            ll=lines[j][2]
            if "|" not in ll:break
            h=cells(ll)
            if len(h)!=len(cs):break
            if row[j]:continue
            if not any(TITLE.fullmatch(z) for _,_,z in h):continue
            field=h[col][2]
            if not safe(field) or not label_matches(field,e["label"]):return None
            if e["label"]=="account_number" and not HOLDER_ROLE.search(field):return None
            choices=[]
            for n in ns(i):
                nc=next((k for k,(x,y,_) in enumerate(cs) if x<=n["start"]-a<y),None)
                if nc is None or not TITLE.fullmatch(h[nc][2]):continue
                qualifiers=[m.group(0) for m in re.finditer(ROLE,field)]
                if qualifiers and h[nc][2] not in qualifiers:continue
                choices.append(ix[span(n)])
            return one(choices)
        return None
    for e in records:
        if e["label"]=="person":continue
        k=key(e);i=at[k];a,b,line=lines[i];lab=e["label"]
        old_id,old_reason=strong[k];c_id,c_reason=candidates[k]
        proposals=[]
        if old_id is not None:proposals.append((old_id,"o3:"+old_reason))
        # Prior-scope / whole-line blocks may be overly broad. Override
        # only via new explicit scoped proof; other guards remain hard.
        permitted=old_id is not None or old_reason in ("no_explicit_scope","prior_scope_change","nonperson_scope")
        if permitted:
            if "|" in line:
                v=table(i,e)
                if v is not None:proposals.append((v,"typed_table_header"))
            else:
                v=inline(i,e)
                if v is not None:proposals.append((v,"bounded_inline"))
                if not ns(i):
                    v=block(i,e)
                    if v is not None:proposals.append((v,"typed_field_scope"))
                if lab=="address" and not ns(i) and i+1<len(lines) and safe(line):
                    if len(ns(i+1))==1:
                        n=ns(i+1)[0];aa,bb,_=lines[i+1]
                        if not text[aa:n["start"]].strip() and ADDRESSEE.fullmatch(text[n["end"]:bb]) and not text[a:e["start"]].strip() and not text[e["end"]:b].strip():
                            proposals.append((ix[span(n)],"postal_addressee"))
        owner=one(v for v,_ in proposals)
        if owner is not None:
            s=(owner,"+".join(sorted({why for _,why in proposals})))
        elif proposals:s=(None,"proposal_conflict")
        else:s=(None,old_reason)
        strict[k]=s
        # Candidate union can retain scoped weak association only if no
        # explicit conflicting owner; never promoted to graph proof.
        if s[0] is not None:wide[k]=s
        elif proposals:wide[k]=s
        elif c_id is not None:wide[k]=(c_id,c_reason if c_reason.startswith("tentative:") else "tentative:"+c_reason)
        else:wide[k]=s
    return subjects,legacy,old,modes,strict,wide

def nodes_for(did,subjects,pred,records):
    by=defaultdict(lambda:defaultdict(set));lookup={key(e):e for e in records}
    for k,(i,_) in pred.items():
        if i is not None:by[i][k[0]].add(lookup[k]["token"])
    out=[]
    for i,s in enumerate(subjects):
        ns=[n for n in s["checks"] if n["label"]=="person"]
        ms=tuple(sorted((did,n["start"],n["end"]) for n in ns))
        nn={n["normalized"] for n in ns};require(len(nn)==1,"NAME_PARITY")
        out.append(dict(id=ms,doc=did,name=next(iter(nn)),role=s["role"],attrs=dict(by[i]),held=bool(s.get("force_hold")),mentions=ms))
    return out

def regroup(mentions,legacy,pred):
    # Diagnostic only: project the identical attribute relation set onto
    # old name/role groups to isolate grouping effect on graph recall.
    owner={span(n):i for i,s in enumerate(legacy) for n in s["checks"] if n["label"]=="person"}
    return {k:(owner[span(mentions[i]["checks"][0])] if i is not None else None,r) for k,(i,r) in pred.items()}

def evaluate(rows,own,scope):
    totals=defaultdict(Counter);by_label=defaultdict(lambda:defaultdict(Counter));why=defaultdict(Counter)
    graph=defaultdict(list);truth={};elapsed=0;counts=Counter()
    for did,family,text,records,pids,ref in rows:
        counts.update(v["status"] for v in ref.values())
        t=time.perf_counter()
        mentions,legacy,old,modes,strict,wide=refine(text,records,own)
        elapsed+=(time.perf_counter()-t)*1000
        preds={"O2":(legacy,old["O2_structure"]),"O3B":(mentions,modes["O3B_explicit_scope"]),
            "O3C":(mentions,modes["O3C_candidate_union"]),"O4_explicit":(mentions,strict),"O4_candidates":(mentions,wide)}
        for mode,(ss,ps) in preds.items():
            require(set(ps)==set(ref),"ATTR_PARITY")
            c,types,rs=grade(ps,ref,ss,pids);totals[mode].update(c);why[mode].update(rs)
            for lab,cs in types.items():by_label[mode][lab].update(cs)
            if scope!="A187" and mode.startswith("O4") and (c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"]+c["missed_person"]):
                emit("O4_CASE",scope=scope,case=did,mode=mode,counts=dict(c),reasons={a+":"+b:n for (a,b),n in rs.items()})
        if scope=="A187":
            for mode,ss,ps in (
                ("O2_grouped",legacy,old["O2_structure"]),
                ("O2_remapped_singletons",mentions,modes["O3A_occurrence_only"]),
                ("O3B_singletons",mentions,modes["O3B_explicit_scope"]),
                ("O3B_regroup_diagnostic",legacy,regroup(mentions,legacy,modes["O3B_explicit_scope"])),
                ("O4_singletons",mentions,strict),
                ("O4_regroup_diagnostic",legacy,regroup(mentions,legacy,strict)),
            ):graph[mode].extend(nodes_for(did,ss,ps,records))
            truth.update({(did,*s):p for s,p in pids.items()})
    emit("O4_INPUT",scope=scope,documents=len(rows),reference=dict(counts),mean_all_modes_ms=elapsed/len(rows))
    for mode,c in totals.items():
        tp=c["correct_person"];bad=c["wrong_person"]+c["unsafe_scope_assignment"]+c["uncertain_assigned"];assigned=tp+bad;g=counts[PERSON]
        require(sum(c.values())==sum(counts.values()) and tp+c["wrong_person"]+c["missed_person"]==g,"SCORE_PARITY")
        emit("O4_RESULT",scope=scope,mode=mode,counts=dict(c),unsafe=bad,precision=tp/assigned if assigned else None,
            recall=tp/g,f1=2*tp/(g+assigned),by_label={k:dict(v) for k,v in by_label[mode].items()},
            reasons={a+":"+b:n for (a,b),n in sorted(why[mode].items())})
    for mode,ns in graph.items():
        ix={n["id"]:i for i,n in enumerate(ns)}
        edges={tuple(sorted((ix[x],ix[y]))) for x,y in full_snapshot(ns)}
        emit("O4_GRAPH_FACTOR",mode=mode,pair_metrics=metrics(truth,pair_predictions(ns,edges,True)),
             scope="diagnostic old grouping unsafe; experimental association anchors, not verified identity")

# New author-labelled counterexamples, kept separate from old 43 regression.
# These are targeted development checks, not a blinded generalization test.
EXTRA=(
 ("new01","field","신청인: [[person|A|가상민]]\n출금 계좌: [[account_number|-|계좌값]]\n연락처: [[phone_number|A|전화값]]"),
 ("new02","field","신청인: [[person|A|가상민]]\n대리인 정보: 별도 지정\n연락처: [[phone_number|?|전화값]]"),
 ("new03","field","대표자: [[person|A|가상민]]\n사업장 주소: [[address|-|주소값]]\n대표자 연락처: [[phone_number|A|전화값]]"),
 ("new04","table","| 성명 | 소속 | 연락처 |\n| --- | --- | --- |\n| [[person|A|가상민]] | 운영팀 | [[email|A|메일값]] |"),
 ("new05","table","| 고객 | 직원 | 연락처 |\n| [[person|A|가상민]] | [[person|B|가상윤]] | [[phone_number|?|전화값]] |"),
 ("new06","inline","대리인: [[person|A|가상민]] / 주민등록번호 [[resident_registration_number|A|주민값]] / 연락처 [[phone_number|A|전화값]]"),
 ("new07","inline","예금주: [[person|A|가상민]] [[account_number|A|계좌값1]], 수수료 수납계좌: [[account_number|-|계좌값2]]"),
 ("new08","letter","[[address|A|주소값]]\n[[person|A|가상민]] 님 귀하\n문의: [[person|B|가상윤]]"),
 ("new09","letter","반송 주소: [[address|?|주소값]]\n[[person|A|가상민]] 님 귀하\n실제 수신인 미확인"),
 ("new10","inline","[[person|A|가상민]]의 전화번호 [[phone_number|-|전화값]]는 본인 번호가 아닌 회사 공용 번호다."),
 ("new11","inline","직원 [[person|A|가상민]]이 고객 [[person|B|가상윤]]의 주소 [[address|B|주소값]]를 확인했다."),
 ("new12","mail","To: [[email|?|메일값1]], [[email|?|메일값2]]\nDear [[person|A|가상민]],\n수신자별 주소 미확인"),
)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",type=Path,required=True);p.add_argument("--reference",type=Path,required=True);p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args();own=team_source(a.fetch_team)
    for scope,fixtures in (("regression",FIXTURES),("additional_challenge",EXTRA)):
        rows=[(cid,family,*decode(src)) for cid,family,src in fixtures]
        evaluate(rows,own,scope)
    evaluate(corpus(a.document_cache.expanduser(),a.reference.expanduser()),own,"A187")
    emit("O4_COMPLETE",scope="development-only; candidate relationships not authorized; no DB/LLM/Holdout2")
if __name__=="__main__":main()
