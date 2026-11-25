"""Offline POS evidence experiment, NOT a runtime release policy.
Uses Q88 cached PERSON spans and invented contrast examples. Never changes
masks/tokens. Gold is used only to score the proposed candidates.
No raw corpus values, names, or token strings are printed.
"""
from collections import Counter
import argparse,hashlib,json,statistics,time
from pathlib import Path
from starter.s2_project_projection import case_nominal,_CASE_ROLE
from starter.s2_pseudonymizer import V7_POLICY
from starter.v7_model_store import read_manifest
from tools.benchmark_s2_prepare import queries,mask
from tools.benchmark_step1 import require
from tools.audit_s2_query_gold import parse_gold

COMMON={"NNG","NNB"}
def evidence(text,e,typed,analyses):
    a,b=e["start"],e["end"]
    tags=[];nominal=[];copula=[]
    for tokens,_ in analyses:
        hits=[t for t in tokens if t.start<b and a<t.start+t.len]
        exact=[t for t in hits if t.start==a and t.start+t.len==b]
        tags.append(sorted({t.tag for t in hits}))
        nominal.append(bool(exact) and all(t.tag in COMMON for t in hits))
        # Require an immediately following copula, not merely a common-noun tag.
        copula.append(any(t.tag=="VCP" and t.start==b for t in tokens))
    anchor=[p for p in typed if p["label"]=="person" and p["end"]<a
            and _CASE_ROLE.fullmatch(text[p["end"]:a])]
    anchored=len(anchor)==1 and not any(p is not anchor[0] and p!=e and
             p["start"]<b and anchor[0]["end"]<p["end"] for p in typed)
    return dict(pos_top1=nominal[0],pos_consensus=all(nominal),
                contextual_pos=anchored and all(nominal) and all(copula),
                narrow_grammar=case_nominal(text,a,b,typed),tags=tags,
                anchored=anchored,copula=copula)

def add(c,signals,expected):
    for name in ("pos_top1","pos_consensus","contextual_pos","narrow_grammar"):
        c[name][("tp" if expected else "fp") if signals[name] else ("fn" if expected else "tn")]+=1

def fixture(text,value,expected,anchor=False):
    a=text.rindex(value);e=dict(start=a,end=a+len(value),label="person")
    return text,e,([dict(start=0,end=3,label="person")] if anchor else [])+[e],expected

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--query-cache",required=True,type=Path);a=p.parse_args()
    try:
        import kiwipiepy
        from kiwipiepy import Kiwi
    except ImportError:raise SystemExit("KIWI_NOT_INSTALLED")
    kiwi=Kiwi(num_workers=1)
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    cache=json.loads(a.query_cache.expanduser().read_text());m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    require(cache["policy"]==V7_POLICY and cache["model_revision"]==m["revision"] and cache["query_sha256"]==hashlib.sha256(qp.read_bytes()).hexdigest() and cache["query_gold"]["sha256"]==hashlib.sha256(gp.read_bytes()).hexdigest(),"CACHE_MISMATCH")
    rows=queries(qp);gold={x["qid"]:x for x in map(json.loads,gp.read_text(encoding="utf-8-sig").splitlines())};cm={r["qid"]:r for r in cache["private_query_offsets"]}
    require(len(rows)==len(gold)==len(cm)==88 and set(cm)==set(gold)=={q["qid"] for q in rows},"QUERY_IDS")
    corpus={n:Counter() for n in ("pos_top1","pos_consensus","contextual_pos","narrow_grammar")};synthetic={n:Counter() for n in corpus};times=[];n=0;partial=0
    for q in rows:
        text=q["text"];qid=q["qid"];r=cm[qid];g=gold[qid]
        require(r["length"]==len(text) and g.get("query",text)==text,"QUERY_TEXT")
        gs,err=parse_gold(text,g.get("query_entities"));require(err is None,"GOLD_PARSE")
        expected=mask(gs,len(text))
        ps=[e for e in r["entities"] if e["label"]=="person"]
        if not ps:continue
        t=time.perf_counter();analysis=kiwi.analyze(text,top_n=3);times.append((time.perf_counter()-t)*1000)
        require(bool(analysis),"NO_POS_ANALYSIS")
        for e in ps:
            a0,b=e["start"],e["end"];n+=1
            outside=not any(expected[a0:b]);partial+=not outside and not all(expected[a0:b])
            sig=evidence(text,e,r["entities"],analysis);add(corpus,sig,outside)
            if outside or any(sig[k] for k in corpus):
                print("POS_CORPUS_CANDIDATE",json.dumps(dict(qid=qid,start=a0,end=b,entirely_outside_gold=outside,**sig),ensure_ascii=False),flush=True)
    fixtures=[
        fixture("가상민 고객 건입니다.","건",True,True),
        fixture("가상민 신청인 건인데요, 기록 요청","건",True,True),
        fixture("가상민 고객건입니다.","건",True,True),
        fixture("가상민 계약자 문의입니다.","문의",True,True),
        fixture("가상민 고객 상담입니다.","상담",True,True),
        fixture("가상민 고객 요청입니다.","요청",True,True),
        fixture("가상민 담당자 기록입니다.","기록",True,True),
        fixture("건 고객님의 기록","건",False),
        fixture("가상 건 고객님","가상 건",False),
        fixture("가상민 고객, 건입니다.","건",False,True),
        fixture("가상민 고객 윤입니다.","윤",False,True),
        fixture("가상민 고객 민입니다.","민",False,True),
        fixture("가상민 고객 건 님 기록","건",False,True),
        fixture("성명: 건입니다.","건",False),
        fixture("고객 건입니다.","건",True),
        fixture("안녕하세요, 상담 기록입니다.","상담",True),
        fixture("상담 고객님 정보","상담",False),
        fixture("문의 님 기록","문의",False)]
    for i,(text,e,typed,expected) in enumerate(fixtures,1):
        sig=evidence(text,e,typed,kiwi.analyze(text,top_n=3));add(synthetic,sig,expected)
        print("POS_FIXTURE",json.dumps(dict(case=i,expected_nonperson=expected,**sig),ensure_ascii=False),flush=True)
    print("POS_PROBE_SUMMARY",json.dumps(dict(kiwi_version=kiwipiepy.__version__,corpus_person_spans=n,
        partial_gold_overlap=partial,corpus={k:dict(v) for k,v in corpus.items()},
        synthetic={k:dict(v) for k,v in synthetic.items()},fixture_count=len(fixtures),
        analysis_mean_ms=statistics.mean(times) if times else None,
        policy="diagnostic_only_no_release"),ensure_ascii=False),flush=True)
if __name__=="__main__":main()
