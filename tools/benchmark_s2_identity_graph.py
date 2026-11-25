"""S2 identity evidence and order-independent linking ablation.
Uses frozen V7 cache and pinned team ownership. No DB/model/LLM.
Gold is used only for scoring, never to construct edges.
Confirmed is experimental: assigned attributes have no ownership Gold.
"""
import argparse, hashlib, json, random, secrets
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path
import subprocess, sys, tempfile, types
from starter.s2_pseudonymizer import make_hmac_token, V7_POLICY, READY
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data

COMMIT="693bc3190ebbbb7f156a986848628afa80c8aba2"
URL="https://github.com/8bitHermitcrab/privacy-rag-chatbot.git"
HASHES={"person_rules":"bae5efeb3632197432c2f49b2b3bdcc78dc573fcacefb0953b7d4b9fdb8bada4",
        "person_ownership":"26863405e0596f2e422c6b4ee13eef1e216d6649e0c7bf4a5e0362ca16a1c88d"}
PERSON_SHA="5acb4443162613891a0d52e08349932fcf8acb9e79e4aceef03803b3bda87fe5"
RRN="resident_registration_number"
PHONE="phone_number"
SECOND={"email","account_number","card_number"}
ANCHORS={RRN,PHONE}|SECOND
UNKNOWN="역할 미확인"

def require(ok,reason):
    if not ok:raise RuntimeError(reason)
def emit(tag,**v):print(tag,json.dumps(v,ensure_ascii=False,sort_keys=True),flush=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def jsonl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
def team_source(fetch):
    def read(repo):
        return {n:subprocess.check_output(["git","-C",str(repo),"show",f"{COMMIT}:starter/{n}.py"],stderr=subprocess.DEVNULL).decode("utf-8") for n in HASHES}
    src=None
    for p in (Path.cwd(),Path.home()/"AegisRAG",Path.home()/"projects/privacy-rag-chatbot"):
        try:src=read(p);break
        except (OSError,subprocess.CalledProcessError):pass
    if src is None and fetch:
        with tempfile.TemporaryDirectory(prefix="s2-identity-source-") as td:
            subprocess.run(["git","init","-q",td],check=True)
            subprocess.run(["git","-C",td,"fetch","--quiet","--depth=1",URL,COMMIT],check=True)
            src=read(td)
    require(src is not None,"PINNED_TEAM_SOURCE_UNAVAILABLE")
    require(all(hashlib.sha256(src[n].encode()).hexdigest()==h for n,h in HASHES.items()),"TEAM_SOURCE_HASH_MISMATCH")
    pkg=types.ModuleType("_s2_graph_team");pkg.__path__=[];sys.modules[pkg.__name__]=pkg
    mods={}
    for n in HASHES:
        full=pkg.__name__+"."+n;mod=types.ModuleType(full);mod.__package__=pkg.__name__
        sys.modules[full]=mod;exec(compile(src[n],f"{COMMIT}:{n}","exec"),mod.__dict__);mods[n]=mod
    return mods["person_ownership"]

def load(cache,ownership):
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";gp=root/"holdout_persons_gold.jsonl"
    docs=load_data("A187",dp);require(sha(gp)==PERSON_SHA,"PERSON_GOLD_HASH")
    gr=jsonl(gp);gm={str(x["id"]):x["persons"] for x in gr}
    dc=json.loads(cache.expanduser().read_text())
    manifest=read_manifest(Path.cwd()/"models/v7_model_release.json")
    require(dc["document_sha256"]==sha(dp) and dc["policy"]==V7_POLICY and dc["model_revision"]==manifest["revision"],"CACHE_MISMATCH")
    dm={str(x["doc_id"]):x for x in dc["private_document_offsets"]}
    ids={str(d["id"]) for d in docs}
    require(len(gm)==len(gr)==len(dm)==len(dc["private_document_offsets"])==len(ids)==187 and set(gm)==set(dm)==ids,"DOC_IDS")
    key=secrets.token_bytes(32);nodes=[];truth={};counts=Counter();orphan=Counter()
    for d in docs:
        did=str(d["id"]);text=d["text"];r=dm[did]
        require(r["length"]==len(text),"DOC_LENGTH")
        if r["decision"]!=READY:continue
        counts["ready_docs"]+=1
        expected={(e["start"],e["end"]):e for e in d["entities"] if e["label"]=="person"}
        g={(e["start"],e["end"]):e for e in gm[did]}
        require(len(g)==len(gm[did])==len(expected) and set(g)==set(expected),"GOLD_SPANS")
        for (a,b),e in g.items():
            require(text[a:b]==e["text"] and isinstance(e["person_id"],str) and bool(e["person_id"]),"GOLD_PERSON")
            truth[did,a,b]=e["person_id"]
        records=[]
        for e in r["entities"]:
            a,b,lab=e["start"],e["end"],e["label"]
            require(type(a) is int and type(b) is int and 0<=a<b<=len(text),"OFFSETS")
            token,norm=make_hmac_token(key,lab,text[a:b]);records.append(dict(label=lab,start=a,end=b,token=token,normalized=norm))
        require({(e["start"],e["end"]) for e in records if e["label"]=="person"}==set(g),"V7_PERSON_PARITY")
        subjects,unassigned=ownership.group_records(text,records)
        counts["unassigned"]+=len(unassigned);orphan.update(e["label"] for e in unassigned)
        counts["attributes"]+=sum(e["label"]!="person" for e in records)
        for s in subjects:
            ms=[e for e in s["checks"] if e["label"]=="person"]
            names={e["normalized"] for e in ms};require(len(names)==1,"SUBJECT_NAME")
            attr=defaultdict(set)
            for e in s["checks"]:
                if e["label"]!="person":attr[e["label"]].add(e["token"]);counts["assigned"]+=1
            mentions=tuple(sorted((did,e["start"],e["end"]) for e in ms))
            nodes.append(dict(id=mentions,doc=did,name=next(iter(names)),role=s["role"],
                attrs=dict(attr),held=bool(s["force_hold"]),mentions=mentions))
    nodes.sort(key=lambda x:x["id"])
    mentions=[m for n in nodes for m in n["mentions"]]
    require(len(mentions)==len(set(mentions))==len(truth)==551 and set(mentions)==set(truth),"MENTION_PARITY")
    require(len(nodes)==440 and counts["ready_docs"]==184 and counts["assigned"]==256 and counts["unassigned"]==84 and counts["attributes"]==340,"OWNERSHIP_BASELINE_PARITY")
    emit("IDENTITY_INPUT",**counts,subjects=len(nodes),unassigned_by_label=dict(orphan),
         mixed_gold_subjects=sum(len({truth[m] for m in n["mentions"]})>1 for n in nodes))
    return nodes,truth

def attrs(n,k):return n["attrs"].get(k,set())
def suffix(a,b):
    # Candidate only, no automatic stripping of surname and no short-name merge.
    s,l=sorted((a,b),key=len)
    return len(s)>=2 and len(l)-len(s) in (1,2) and l.endswith(s) and all("가"<=x<="힣" for x in l)
def role_known(n):return n["role"] not in ("",UNKNOWN,None)
def evidence(a,b,shared):
    match={k for k in ANCHORS|{"address"} if attrs(a,k)&attrs(b,k)}
    usable={k for k in match&ANCHORS if (attrs(a,k)&attrs(b,k))-shared[k]}
    conflict=bool(attrs(a,RRN) and attrs(b,RRN) and not attrs(a,RRN)&attrs(b,RRN))
    role_same=role_known(a) and role_known(b) and a["role"]==b["role"]
    role_diff=role_known(a) and role_known(b) and a["role"]!=b["role"]
    return dict(same=a["name"]==b["name"],suffix=suffix(a["name"],b["name"]),
        shared=match,usable=usable,rrn_conflict=conflict,role_same=role_same,role_diff=role_diff,
        bad=a["held"] or b["held"] or len(attrs(a,RRN))>1 or len(attrs(b,RRN))>1,
        same_doc=a["doc"]==b["doc"])
def pair_predictions(nodes,edges,local=False):
    out=set()
    if local:
        for n in nodes:out.update(tuple(sorted(x)) for x in combinations(n["mentions"],2))
    for i,j in edges:
        out.update(tuple(sorted((a,b))) for a,b in product(nodes[i]["mentions"],nodes[j]["mentions"]))
    return out
def metrics(truth,guessed):
    c=Counter()
    for a,b in combinations(sorted(truth),2):
        actual=truth[a]==truth[b];pred=(a,b) in guessed
        if not(actual or pred):continue
        k="tp" if actual and pred else "fp" if pred else "fn"
        c["all",k]+=1
        if a[0]!=b[0]:c["cross_doc",k]+=1
    out={}
    for scope in ("all","cross_doc"):
        tp,fp,fn=[c[scope,k] for k in ("tp","fp","fn")]
        out[scope]=dict(tp=tp,fp=fp,fn=fn,precision=tp/(tp+fp) if tp+fp else None,
            recall=tp/(tp+fn) if tp+fn else None,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)
    return out

def graph(nodes,edge_set):
    # Batch connected components. Reject the ENTIRE conflicted component
    # instead of choosing the first accepted edge. Thus permutation invariant.
    adj=[set() for _ in nodes]
    for i,j in edge_set:adj[i].add(j);adj[j].add(i)
    groups=[];seen=set();rejected=0
    for i in range(len(nodes)):
        if i in seen:continue
        seen.add(i);todo=[i];group=[]
        while todo:
            x=todo.pop();group.append(x)
            for y in adj[x]:
                if y not in seen:seen.add(y);todo.append(y)
        rrns=set().union(*(attrs(nodes[x],RRN) for x in group))
        if len(group)>1 and (len(rrns)>1 or any(nodes[x]["held"] for x in group)):
            rejected+=1;groups.extend([[x] for x in group])
        else:groups.append(sorted(group))
    pairs=set()
    for group in groups:pairs.update(combinations(sorted(group),2))
    linked=sum(len(nodes[x]["mentions"]) for g in groups if len({nodes[x]["doc"] for x in g})>1 for x in g)
    return pairs,rejected,linked,len(groups)

def experiments(nodes,truth):
    # Shared identifiers across different name values are not automatic anchors.
    # Aliases can also produce this signal: intentionally conservative.
    owners=defaultdict(set)
    for n in nodes:
        for k in ANCHORS:
            for t in attrs(n,k):owners[k,t].add(n["name"])
    shared={k:{t for (lab,t),ns in owners.items() if lab==k and len(ns)>1} for k in ANCHORS}
    emit("ANCHOR_INVENTORY",shared_across_name_values={k:len(v) for k,v in shared.items()},
        subjects_by_attribute={k:sum(bool(attrs(n,k)) for n in nodes) for k in sorted(ANCHORS|{"address"})},
        multi_rrn_subjects=sum(len(attrs(n,RRN))>1 for n in nodes))
    edges={k:set() for k in ("batch_strict","batch_balanced","batch_relaxed","batch_role_soft",
         "candidate_same_name","candidate_name_role","candidate_name_no_rrn_conflict",
         "candidate_name_role_or_anchor","candidate_alias","candidate_union")}
    observations=defaultdict(Counter)
    name_pids=defaultdict(set)
    for n in nodes:name_pids[n["name"]].update(truth[m] for m in n["mentions"])
    for i,j in combinations(range(len(nodes)),2):
        a,b=nodes[i],nodes[j];e=evidence(a,b,shared);same=e["same"];alias=e["suffix"]
        if not(same or alias or e["shared"]):continue
        safe=not(e["bad"] or e["rrn_conflict"])
        if same and safe:
            rrn=RRN in e["usable"];phone=PHONE in e["usable"];secondary=len(e["usable"]&SECOND)>=2
            if not e["role_diff"]:
                if rrn:edges["batch_strict"].add((i,j))
                if rrn or phone:edges["batch_balanced"].add((i,j))
                if rrn or phone or secondary:edges["batch_relaxed"].add((i,j))
            if rrn or phone or secondary:edges["batch_role_soft"].add((i,j))
        if same:
            edges["candidate_same_name"].add((i,j))
            if e["role_same"]:edges["candidate_name_role"].add((i,j))
            if safe:edges["candidate_name_no_rrn_conflict"].add((i,j))
            if safe and (e["role_same"] or e["usable"]):edges["candidate_name_role_or_anchor"].add((i,j))
        if not same and alias and safe and (e["same_doc"] or bool(e["shared"]&ANCHORS)):
            edges["candidate_alias"].add((i,j))
        # Gold is used only below: aggregate observed evidence, never feed it into edges.
        for x,y in product(a["mentions"],b["mentions"]):
            if x[0]==y[0]:continue
            actual=truth[x]==truth[y]
            category=("same_name_ambiguous" if len(name_pids[a["name"]])>1 else "same_name_other") if same else "different_name"
            c=observations[category,"same_pid" if actual else "different_pid"]
            c["pairs"]+=1;c["same_role"]+=e["role_same"];c["different_role"]+=e["role_diff"]
            c["rrn_conflict"]+=e["rrn_conflict"];c["suffix"]+=alias
            c["any_shared_anchor"]+=bool(e["shared"]&ANCHORS)
            c["any_usable_anchor"]+=bool(e["usable"])
            for k in e["shared"]:c["shared_"+k]+=1
    for (cat,rel),c in sorted(observations.items()):emit("PAIR_EVIDENCE",category=cat,relation=rel,**c)
    edges["candidate_union"]=edges["candidate_name_no_rrn_conflict"]|edges["candidate_alias"]
    for mode,es in edges.items():
        if mode.startswith("batch"):
            ps,reject,linked,ng=graph(nodes,es)
            # Verify graph partitions are invariant under relabeling node order.
            rng=random.Random(11);perm=list(range(len(nodes)));rng.shuffle(perm)
            ix={old:new for new,old in enumerate(perm)}
            shuffled=[nodes[i] for i in perm]
            newedges={tuple(sorted((ix[i],ix[j]))) for i,j in es}
            nps,nreject,nlinked,nng=graph(shuffled,newedges)
            back={tuple(sorted((perm[i],perm[j]))) for i,j in nps}
            require(back==ps and (reject,linked,ng)==(nreject,nlinked,nng),"ORDER_REGRESSION")
            emit("BATCH_IDENTITY",mode=mode,positive_subject_edges=len(es),groups=ng,
                rejected_conflicting_components=reject,cross_doc_linked_mentions=linked,
                order_invariant=True,pair_metrics=metrics(truth,pair_predictions(nodes,ps,True)))
        else:
            emit("CANDIDATE_LINKS",mode=mode,subject_edges=len(es),
                pair_metrics=metrics(truth,pair_predictions(nodes,es,True)),scope="direct possible links only; not PID merge")
    # Evidence ceiling: include all actual cross-doc same-person pairs even if
    # not generated by name/suffix/shared-key blocking.
    by_pid=defaultdict(list)
    for i,n in enumerate(nodes):
        for m in n["mentions"]:by_pid[truth[m]].append((i,m))
    coverage=Counter()
    for group in by_pid.values():
        for (i,m),(j,k) in combinations(group,2):
            if m[0]==k[0]:continue
            e=evidence(nodes[i],nodes[j],shared)
            coverage["true_cross_doc_pairs"]+=1
            coverage["same_name"]+=e["same"];coverage["different_name"]+=not e["same"]
            coverage["any_shared_anchor"]+=bool(e["shared"]&ANCHORS)
            coverage["same_name_usable_anchor"]+=e["same"] and bool(e["usable"])
            coverage["different_name_shared_anchor"]+=not e["same"] and bool(e["shared"]&ANCHORS)
            coverage["different_name_suffix"]+=not e["same"] and e["suffix"]
            coverage["same_name_same_role"]+=e["same"] and e["role_same"]
            coverage["no_name_or_suffix_or_shared_anchor"]+=not(e["same"] or e["suffix"] or e["shared"]&ANCHORS)
    require(coverage["true_cross_doc_pairs"]==2209,"GOLD_PAIR_PARITY")
    emit("TRUE_PAIR_EVIDENCE_COVERAGE",**coverage,scope="Gold diagnostic, not runtime linking")
def self_test():
    def n(i,rrn=(),phone=(),role="고객/신청인"):
        return dict(doc=str(i),name="가상민",role=role,attrs={RRN:set(rrn),PHONE:set(phone)},held=False,mentions=((str(i),0,3),))
    ns=[n(0,["r1"],["p1"]),n(1,[],["p1","p2"]),n(2,["r2"],["p2"])]
    p,r,linked,g=graph(ns,{(0,1),(1,2)})
    require(not p and r==1 and linked==0 and g==3,"TRANSITIVE_RRN_CONFLICT")
    p,r,linked,g=graph(ns[:2],{(0,1)})
    require(p=={(0,1)} and r==0 and linked==2,"VALID_LINK")
    require(suffix("민준","김민준") and not suffix("준","김민준"),"ALIAS_CANDIDATE")
    emit("IDENTITY_GRAPH_SELF_TEST",passed=3)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--document-cache",type=Path,required=True);p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args();self_test();nodes,truth=load(a.document_cache,team_source(a.fetch_team));experiments(nodes,truth)
    emit("IDENTITY_GRAPH_COMPLETE",scope="A187 READY development only; same team ownership; no manual decisions or DB; no verified attribute ownership or runtime changes")
if __name__=="__main__":main()
