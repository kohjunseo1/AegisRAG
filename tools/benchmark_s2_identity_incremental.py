"""Incremental identity experiment. No DB writes, model or LLM.
Same batch_role_soft rules as benchmark_s2_identity_graph, with affected
name-bucket rebuilds. Stable mention keys, replaceable link snapshots.
Not a production PID allocator, ownership verifier or egress policy.
"""
import argparse, random, time
from collections import defaultdict, Counter
from itertools import combinations
from pathlib import Path
from tools.benchmark_s2_identity_graph import (
    load, team_source, graph, evidence, attrs, pair_predictions, metrics,
    require, emit, ANCHORS, RRN, PHONE, SECOND, suffix,
)

def owners(nodes):
    out=defaultdict(set)
    for n in nodes:
        for k in ANCHORS:
            for t in attrs(n,k):out[k,t].add(n["name"])
    return out

def shared_keys(own):
    return {k:{t for (lab,t),ns in own.items() if lab==k and len(ns)>1} for k in ANCHORS}

def bucket_pairs(nodes, shared):
    ns=sorted(nodes,key=lambda n:n["id"])
    es=set()
    for i,j in combinations(range(len(ns)),2):
        e=evidence(ns[i],ns[j],shared)
        if e["same"] and not(e["bad"] or e["rrn_conflict"]) and (
            RRN in e["usable"] or PHONE in e["usable"] or len(e["usable"]&SECOND)>=2):
            es.add((i,j))
    pairs,rejected,_,_=graph(ns,es)
    return {tuple(sorted((ns[i]["id"],ns[j]["id"]))) for i,j in pairs},rejected

def full_snapshot(nodes):
    shared=shared_keys(owners(nodes))
    by_name=defaultdict(list)
    for n in nodes:by_name[n["name"]].append(n)
    result=set()
    for ns in by_name.values():result.update(bucket_pairs(ns,shared)[0])
    return result

class Registry:
    def __init__(self):
        self.docs={};self.buckets={};self.links=set();self.generation=0
    def update(self, replacements):
        """Whole-document replacement, including empty tuple for removal.
        Identical replays are no-op. A changed source invalidates old mentions.
        Callers must supply complete documents, not isolated text fragments.
        """
        old_nodes=[n for ns in self.docs.values() for n in ns]
        old_owners=owners(old_nodes)
        dirty=set()
        proposed=dict(self.docs)
        for doc,items in replacements.items():
            ns=tuple(sorted(items,key=lambda n:n["id"]))
            require(all(n["doc"]==doc for n in ns),"DOC_OWNER")
            require(len({n["id"] for n in ns})==len(ns),"DUPLICATE_MENTION_KEY")
            prev=proposed.get(doc,())
            if prev==ns:continue
            dirty.update(n["name"] for n in prev+ns)
            if ns:proposed[doc]=ns
            else:proposed.pop(doc,None)
        if not dirty:return dict(dirty_buckets=0,added_links=0,revoked_links=0,changed=False)
        new_nodes=[n for ns in proposed.values() for n in ns]
        new_owners=owners(new_nodes)
        for key in old_owners.keys()|new_owners.keys():
            before=old_owners.get(key,set());after=new_owners.get(key,set())
            # Shared/non-shared status can change for old, unrelated buckets.
            if (len(before)>1)!=(len(after)>1):dirty.update(before|after)
        shared=shared_keys(new_owners);groups=defaultdict(list)
        for n in new_nodes:
            if n["name"] in dirty:groups[n["name"]].append(n)
        next_buckets=dict(self.buckets);rejected=0
        for name in dirty:
            if name not in groups:next_buckets.pop(name,None);continue
            p,r=bucket_pairs(groups[name],shared);next_buckets[name]=p;rejected+=r
        next_links=set().union(*next_buckets.values()) if next_buckets else set()
        delta=dict(dirty_buckets=len(dirty),added_links=len(next_links-self.links),
                   revoked_links=len(self.links-next_links),changed=True,rejected_components=rejected)
        # Publish a new snapshot only after all buckets are calculated.
        self.docs,self.buckets,self.links=proposed,next_buckets,next_links
        self.generation+=1
        return delta

def assert_snapshot(r):
    ns=[n for seq in r.docs.values() for n in seq]
    require(r.links==full_snapshot(ns),"INCREMENTAL_FULL_PARITY")

def self_test():
    def node(doc,name="가상민",rrn=(),phone=(),role="고객/신청인"):
        mid=((doc,0,len(name)),)
        return dict(id=mid,doc=doc,name=name,role=role,attrs={RRN:set(rrn),PHONE:set(phone)},held=False,mentions=mid)
    a=node("a",rrn=["r1"],phone=["p1"])
    b=node("b",phone=["p1","p2"])
    c=node("c",rrn=["r2"],phone=["p2"])
    r=Registry()
    r.update({"a":[a],"b":[b]});assert_snapshot(r);require(len(r.links)==1,"INITIAL_LINK")
    d=r.update({"c":[c]});assert_snapshot(r);require(not r.links and d["revoked_links"]==1,"LATE_CONFLICT_REVOKE")
    r.update({"c":[]});assert_snapshot(r);require(len(r.links)==1,"DELETE_RECONCILE")
    x=node("x",name="가상윤",phone=["p1"])
    d=r.update({"x":[x]});assert_snapshot(r);require(not r.links and d["revoked_links"]==1,"LATE_SHARED_ANCHOR_REVOKE")
    r.update({"x":[]});assert_snapshot(r);require(len(r.links)==1,"SHARED_ANCHOR_REMOVAL")
    b2=node("b",phone=["p3"])
    r.update({"b":[b2]});assert_snapshot(r);require(not r.links,"DOCUMENT_CORRECTION")
    gen=r.generation;d=r.update({"b":[b2]});require(not d["changed"] and gen==r.generation,"DUPLICATE_REPLAY")
    r.update({"b":[b]});assert_snapshot(r);require(len(r.links)==1,"REPLACEMENT_RESTORE")
    emit("INCREMENTAL_SELF_TEST",passed=8,scope="synthetic, assigned anchor assumptions")

def run(nodes,truth):
    docs=defaultdict(list)
    for n in nodes:docs[n["doc"]].append(n)
    all_ids=sorted(docs);reference=full_snapshot(nodes)
    idx={n["id"]:i for i,n in enumerate(nodes)}
    expected=metrics(truth,pair_predictions(nodes,{tuple(sorted((idx[a],idx[b]))) for a,b in reference},True))
    require(expected["cross_doc"]["tp"]==255 and expected["cross_doc"]["fp"]==0 and expected["cross_doc"]["fn"]==1954,"PREVIOUS_ROLE_SOFT_PARITY")
    for size,seed in ((1,0),(5,11),(20,23),(50,37),(len(all_ids),0)):
        ids=list(all_ids)
        if seed:random.Random(seed).shuffle(ids)
        r=Registry();total=Counter();ms=[]
        for start in range(0,len(ids),size):
            batch={d:docs[d] for d in ids[start:start+size]}
            t=time.perf_counter();delta=r.update(batch);ms.append((time.perf_counter()-t)*1000)
            total.update({k:v for k,v in delta.items() if type(v) is int})
            assert_snapshot(r)
        require(r.links==reference,"FINAL_BATCH_PARITY")
        gen=r.generation
        require(not r.update({d:docs[d] for d in ids[-size:]})["changed"] and gen==r.generation,"REPLAY_PARITY")
        emit("INCREMENTAL_SCENARIO",batch_size=size,seed=seed,documents_with_subjects=len(ids),
            snapshots=r.generation,final_pair_metrics=expected,final_parity=True,
            replay_noop=True,max_update_ms=max(ms),mean_update_ms=sum(ms)/len(ms),**total)
    # Broad suffix-only candidates are a recall ceiling, never identity links.
    es={(i,j) for i,j in combinations(range(len(nodes)),2) if nodes[i]["name"]==nodes[j]["name"] or suffix(nodes[i]["name"],nodes[j]["name"])}
    emit("BROAD_ALIAS_CANDIDATE",pair_metrics=metrics(truth,pair_predictions(nodes,es,True)),
         scope="direct candidates only; no PID, no target-query authorization")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",type=Path,required=True);p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args();self_test();nodes,truth=load(a.document_cache,team_source(a.fetch_team));run(nodes,truth)
    emit("INCREMENTAL_COMPLETE",scope="offline affected name-bucket refresh; complete docs; no DB persistence or stable production PID allocation")
if __name__=="__main__":main()
