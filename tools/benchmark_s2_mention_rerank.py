"""Mention-centered reranking ablation, same AR0 candidate pool.
Local models only. Windows are relevance features, not proven attribution.
"""
import argparse,json,math,re,secrets,time,gc,os
from collections import Counter,defaultdict
from pathlib import Path
from tools.benchmark_s2_ownership_o4 import refine,nodes_for
from tools.benchmark_s2_ownership_o3 import corpus
from tools.benchmark_s2_identity_graph import team_source,require,emit,sha,suffix
from tools.benchmark_s2_identity_incremental import full_snapshot
from tools.audit_s2_query_identity_evidence import role_of
from tools.benchmark_s2_prepare import queries
from tools.inspect_s2_protection_private import rebuild,overlap
from starter.s2_pseudonymizer import READY,REVIEW,BLOCKED,V7_POLICY,make_hmac_token,canonicalize
from starter.s2_query_controller import document_region_keys
from starter.s2_numeric_recovery import plans
from starter.s2_project_projection import project
from starter.v7_model_store import read_manifest

UNKNOWN="역할 미확인"
def terms(text,intervals):
    # Local-only BM25 on text outside STRICT protection union.
    # Preserve offsets and split at removed spans; no original values in index.
    chars=list(text)
    for e in intervals:
        for i in range(e["start"],e["end"]):chars[i]=" "
    out=[]
    for w in re.findall(r"[가-힣A-Za-z0-9]+","".join(chars).lower()):
        out.append("w:"+w)
        out.extend("g:"+w[i:i+n] for n in (2,3) for i in range(len(w)-n+1))
    return Counter(out)

class Lexical:
    def __init__(self,docs):
        self.docs=docs;self.n=len(docs);self.avg=sum(sum(x.values()) for x in docs.values())/self.n
        df=Counter(t for c in docs.values() for t in c)
        self.idf={t:math.log(1+(self.n-d+.5)/(d+.5)) for t,d in df.items()}
    def score(self,q):
        out={}
        for did,c in self.docs.items():
            norm=1.2*(.25+.75*sum(c.values())/max(self.avg,1))
            out[did]=sum(self.idf.get(t,0)*c[t]*2.2/(c[t]+norm) for t in q if c[t])
        return out

def ranked(ds,score,lex):
    return sorted(ds,key=lambda d:(-score.get(d,0),-lex.get(d,0),d))
def rrf(*ranks):
    out=defaultdict(float)
    for rank in ranks:
        for i,d in enumerate(rank,1):out[d]+=1/(60+i)
    return sorted(out,key=lambda d:(-out[d],d))

def evaluate(rank,rel,conf,targets,matched,doc_pids):
    # rank can be empty; full denominator retained, conditional ready separately.
    c=dict(candidates=len(rank),rel_candidates=len(set(rank)&rel),rel_total=len(rel),
           hit1=int(bool(rank) and rank[0] in rel),hit5=int(bool(set(rank[:5])&rel)),
           recall5=len(set(rank[:5])&rel)/len(rel) if rel else 0,
           rr=next((1/i for i,d in enumerate(rank,1) if d in rel),0.0),
           confusable5=len(set(rank[:5])&conf))
    if targets:
        all_target=lambda d:targets<=matched.get(d,set())
        c.update(target_rel_candidates=sum(all_target(d) for d in set(rank)&rel),
                 target_hit1=int(bool(rank) and all_target(rank[0])),
                 target_hit5=int(any(all_target(d) for d in rank[:5])),
                 wrong_person_docs5=sum(not targets<=doc_pids.get(d,set()) for d in rank[:5]),
                 target_unmatched_docs5=sum(not all_target(d) for d in rank[:5]),
                 mixed_matching_docs5=sum(bool(matched.get(d,set())-targets) and bool(matched.get(d,set())&targets) for d in rank[:5]),
                 same_person_nonrelevant5=sum(d not in rel and targets<=doc_pids.get(d,set()) for d in rank[:5]))
    return c


MODELS={"koe5":"nlpai-lab/KoE5","kure":"nlpai-lab/KURE-v1","bge":"BAAI/bge-m3"}
def masked(text,record):
    chars=list(text)
    for e in record["mask_intervals"]:
        a,b=e["start"],e["end"];require(0<=a<b<=len(chars),"MASK_OFFSETS")
        chars[a:b]=[" " for _ in range(b-a)]
    for e in record["entities"]:
        require(all(ch==" " for ch in chars[e["start"]:e["end"]]),"TYPED_MASK_COVERAGE")
    return " ".join("".join(chars).split())

def kiwi_terms(kiwi,text):
    return Counter(t.form.lower() for t in kiwi.tokenize(text)
                   if t.tag.startswith(("N","V","M","SL","SN","XR")))

def dense_scores(name,docs,qs,batch,threads):
    # HF downloads are model files only; all texts are encoded locally.
    import torch
    import numpy as np
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer,AutoModel
    torch.set_num_threads(threads)
    model_id=MODELS[name]
    require(name=="bge","BGE_ONLY")
    rev="9a0624b896d81da7492a910ffa53731274b6cf3d"
    emit("DR_MODEL_START",model=name,repo=model_id,revision=rev,device="cpu",batch=batch,local_files_only=True)
    t0=time.perf_counter()
    folder=Path(snapshot_download(model_id,revision=rev,token=False,local_files_only=True))
    # Read model's declared pooling instead of assuming same pooling for all.
    require((folder/"model.safetensors").is_file() or (folder/"model.safetensors.index.json").is_file(),"SAFE_WEIGHTS_MISSING:"+name)
    pooling=json.loads((folder/"1_Pooling/config.json").read_text())
    modes=[k for k,v in pooling.items() if k.startswith("pooling_mode_") and v is True]
    require(len(modes)==1 and modes[0] in ("pooling_mode_mean_tokens","pooling_mode_cls_token"),"POOLING_UNSUPPORTED:"+name)
    mode=modes[0]
    tokenizer=AutoTokenizer.from_pretrained(str(folder),local_files_only=True,trust_remote_code=False)
    model=AutoModel.from_pretrained(str(folder),local_files_only=True,trust_remote_code=False,
                                   use_safetensors=True,torch_dtype=torch.float32).eval()
    emit("DR_MODEL_LOADED",model=name,pooling=mode,load_seconds=time.perf_counter()-t0)
    def encode(items,kind):
        values=[];truncated=0;t=time.perf_counter()
        prefix=("query: " if kind=="query" else "passage: ") if name=="koe5" else ""
        with torch.inference_mode():
            for start in range(0,len(items),batch):
                texts=[prefix+x for x in items[start:start+batch]]
                raw=tokenizer(texts,add_special_tokens=True,truncation=False,verbose=False)
                truncated+=sum(len(ids)>512 for ids in raw["input_ids"])
                x=tokenizer(texts,padding=True,truncation=True,max_length=512,return_tensors="pt")
                h=model(**x).last_hidden_state
                if mode=="pooling_mode_cls_token":v=h[:,0]
                else:
                    mask=x["attention_mask"].unsqueeze(-1)
                    v=(h*mask).sum(1)/mask.sum(1).clamp_min(1)
                v=torch.nn.functional.normalize(v.float(),p=2,dim=1)
                values.append(v.cpu().numpy())
                if start==0 or (start//batch+1)%10==0 or start+batch>=len(items):
                    emit("DR_PROGRESS",model=name,kind=kind,done=min(start+batch,len(items)),total=len(items))
        result=np.concatenate(values,axis=0);require(np.isfinite(result).all(),"NONFINITE_EMBEDDINGS")
        emit("DR_ENCODE",model=name,kind=kind,rows=len(items),dimension=result.shape[1],
             truncated=truncated,seconds=time.perf_counter()-t,mean_ms=(time.perf_counter()-t)*1000/len(items))
        return result
    dids=sorted(docs);qids=sorted(qs)
    dv=encode([docs[d] for d in dids],"document");qv=encode([qs[q] for q in qids],"query")
    mat=qv@dv.T
    out={qid:{d:float(v) for d,v in zip(dids,row)} for qid,row in zip(qids,mat)}
    del model,tokenizer,dv,qv,mat;gc.collect()
    emit("DR_MODEL_DONE",model=name,revision=rev,total_seconds=time.perf_counter()-t0)
    return out

def fusion(weighted,limit=20):
    out=defaultdict(float)
    for rank,w in weighted:
        for i,d in enumerate(rank[:limit],1):out[d]+=w/(60+i)
    return out

def condition_ranks(semantic,name_sets,required,region_sets,exact_sets):
    # One slot per distinct query name. Counts express coverage, not identity
    # probabilities. Positive evidence only; no global role/graph bonus.
    # Missing/ambiguous values stay fallback candidates, not verified negatives.
    ds=set(semantic).union(*name_sets,*required,*region_sets)
    cov=lambda d,sets:sum(d in s for s in sets)
    def tier(d):
        return (cov(d,required),cov(d,region_sets),cov(d,name_sets))
    # Exact vs suffix is a small secondary signal, never precedes semantic rank.
    strong=sorted(ds,key=lambda d:(tuple(-v for v in tier(d)),-semantic.get(d,0),-cov(d,exact_sets),d))
    # Soft condition boost compared against strict priority ordering. Each
    # available condition family contributes at most one weighted RRF term.
    def soft(d):
        return semantic.get(d,0)+sum((cov(d,s)/len(s))/61 for s in (required,region_sets,name_sets) if s)
    return strong,sorted(ds,key=lambda d:(-soft(d),-cov(d,exact_sets),d))

def self_test():
    assert masked("A123 B",{"mask_intervals":[{"start":1,"end":4}],"entities":[{"start":1,"end":4}]})=="A B"
    a,b=condition_ranks({"wrong":.03,"right":.01},[{"right"}],[{"right"}],[],[{"right"}])
    assert a[0]=="right" and set(a)==set(b)=={"right","wrong"}
    a,b=condition_ranks({"a":.03,"ab":.01},[{"a","ab"},{"ab"}],[],[],[])
    assert a[0]=="ab"
    assert list(fusion([(["x","y"],1)],1))==["x"]
    emit("DR_SELF_TEST",passed=4)



def equal_rank_rrf(pool,channels):
    """Competition ranks inside pool. Ties receive identical score.
    Lexical zero is absent evidence; dense may be negative and still ranks.
    """
    out=dict.fromkeys(pool,0.0)
    for scores,weight,positive_only in channels:
        ordered=sorted((d for d in pool if d in scores and
                        (not positive_only or scores[d]>0)),
                       key=lambda d:(-scores[d],d))
        prev=None;rank=0
        for j,d in enumerate(ordered,1):
            v=scores[d]
            if j==1 or v!=prev:rank=j
            out[d]+=weight/(60+rank);prev=v
    return out

def cov(d,sets):
    return sum(d in s for s in sets)

def adaptive_modes(ds,kl,lex,dr,kr,lex_rank,name_sets,required,region_sets,exact_sets,
                   qattrs,qnames,nodes,wide_nodes,broad,has_text):
    # Reproduce old candidate pool exactly. Do not apply any Gold-derived
    # document, query type or entity ID in these functions.
    old_sem=fusion([(dr,2),(kr,1)],20)
    old,_=condition_ranks(old_sem,name_sets,required,region_sets,exact_sets)
    pool=set(old)
    explicit=set().union(*name_sets,*required,*region_sets)
    lex_pool=set(kr[:20])|explicit
    new_pool=pool|set(lex_rank[:20])
    def tier(d):
        return (cov(d,required),cov(d,region_sets),cov(d,name_sets))
    def order(p,s):
        return sorted(p,key=lambda d:(tuple(-x for x in tier(d)),-s.get(d,0),
                                      -cov(d,exact_sets),d))
    # Matched candidate pool control isolates rank contribution of dense.
    kr_only=lambda p:equal_rank_rrf(p,[(kl,1,True)] if has_text else [])
    dense_kiwi=lambda p:equal_rank_rrf(p,[(ds,2,False),(kl,1,True)] if has_text else [])
    all_channels=lambda p:equal_rank_rrf(p,[(ds,2,False),(kl,1,True),(lex,1,True)] if has_text else [])
    modes={"AR0_previous_tier":old,
           "AR1_lexical_same_pool":order(pool,kr_only(pool)),
           "AR1b_lexical_only_pool":order(lex_pool,kr_only(lex_pool)),
           "AR2_full_candidate_rank":order(pool,dense_kiwi(pool)),
           "AR3_dual_lexical_union":order(new_pool,all_channels(new_pool))}
    # Separate non-address typed identifiers from address/region and names.
    # No assumed universal OR/AND parser: counts are positive evidence, not
    # hard filters. Unmatched conditions remain fallbacks in full rankings.
    attrs=list(dict.fromkeys(qattrs))
    require(len(attrs)==len(required),"ATTR_SLOT_PARITY")
    ids=[s for (lab,_),s in zip(attrs,required) if lab!="address"]
    addresses=[s for (lab,_),s in zip(attrs,required) if lab=="address"]
    route="identifier" if ids else "address" if addresses or region_sets else "name" if name_sets else "semantic"
    if sum(bool(x) for x in (ids,addresses or region_sets,name_sets))>1:route="mixed"
    def route_tier(d):
        # Address token and region are complementary, not independent
        # proofs of person identity. Exact address match precedes region.
        return (cov(d,ids),cov(d,name_sets),cov(d,addresses),cov(d,region_sets))
    # Same occurrence association is only a ranking feature. Explicit
    # O4 relation may be incorrect; tentative links never establish identity.
    owner=defaultdict(int);tentative=defaultdict(int)
    if len(name_sets)==1 and ids:
        id_attrs=[(lab,tok) for lab,tok in attrs if lab!="address"]
        for i in broad:
            n=nodes[i];w=wide_nodes[i]
            x=sum(tok in n["attrs"].get(lab,set()) for lab,tok in id_attrs)
            y=sum(tok in w["attrs"].get(lab,set()) for lab,tok in id_attrs)
            owner[n["doc"]]=max(owner[n["doc"]],x)
            tentative[n["doc"]]=max(tentative[n["doc"]],y)
    # Compute semantic RRF inside each condition tier: unrelated docs
    # outside that tier cannot occupy its ranks. All candidate scores exist.
    buckets=defaultdict(set)
    for d in new_pool:buckets[route_tier(d)].add(d)
    adaptive=[];rank_scores={}
    for t in sorted(buckets,reverse=True):
        p=buckets[t]
        s=all_channels(p)
        rank_scores.update(s)
        adaptive.extend(sorted(p,key=lambda d:(-s[d],-cov(d,exact_sets),d)))
    modes["AR4_adaptive_tier"]=adaptive
    # Compare two local O4 association tiers only when explicit identifiers
    # and a single name coexist. All other queries preserve AR4 ordering.
    modes["AR5_occurrence_evidence"]=sorted(adaptive,key=lambda d:(
        tuple(-x for x in route_tier(d)),-owner[d],-tentative[d],
        -rank_scores[d],-cov(d,exact_sets),d))
    # Diagnostic selective retrieval, NOT Evidence Gate. Restrict only
    # single-condition queries; preserve compound/multi-name fallback.
    # A matching document is not necessarily safe or relevant to answer.
    active=ids+addresses+name_sets
    if not active and region_sets:active=region_sets
    full=modes["AR5_occurrence_evidence"]
    if len(active)==1 and not (region_sets and (ids or name_sets)):
        shortlist=[d for d in full if d in active[0] or
                   (bool(addresses) and any(d in s for s in region_sets))][:5]
    else:shortlist=full[:5]
    modes["AR6_condition_shortlist"]=shortlist
    return modes,route,{"candidate_pool":len(new_pool),"baseline_pool":len(pool),
                        "same_occurrence_docs":sum(v>0 for v in owner.values()),
                        "tentative_occurrence_docs":sum(v>0 for v in tentative.values()),
                        "shortlist_count":len(shortlist),"semantic_text":has_text}

def adaptive_self_test():
    s=equal_rank_rrf({"a","b","c"},[({"a":2,"b":2,"c":0},1,True)])
    assert s["a"]==s["b"] and s["c"]==0
    assert equal_rank_rrf({"a","b"},[({"a":-.2,"b":-.1},1,False)])["b"]>equal_rank_rrf({"a","b"},[({"a":-.2,"b":-.1},1,False)])["a"]
    m,r,x=adaptive_modes({"a":.1,"b":.9},{"a":1,"b":4},{"a":1},["b","a"],["b","a"],["a"],[{"a"}],[],[],[{"a"}],[],[("name","")],[],[],set(),True)
    assert r=="name" and m["AR4_adaptive_tier"][0]=="a" and m["AR6_condition_shortlist"]==["a"]
    m,r,x=adaptive_modes({"a":.1,"b":.9},{},{},["b","a"],[],[],[],[{"a"}],[],[],[("phone_number","t")],[],[],[],set(),False)
    assert r=="identifier" and m["AR4_adaptive_tier"][0]=="a"
    m,r,x=adaptive_modes({"a":.1,"b":.9},{},{},["b","a"],[],[],[{"a"},{"b"}],[],[],[{"a"},{"b"}],[],[("a",""),("b","")],[],[],set(),True)
    assert r=="name" and set(m["AR6_condition_shortlist"])=={"a","b"}
    emit("AR_SELF_TEST",passed=5)



RR_REPO="BAAI/bge-reranker-v2-m3"
RR_REV="b9a8f459d786a86f171264d4b075572506495226"

def marked_view(text,record,names):
    # Render ONLY the complement of the original strict mask union, plus
    # synthetic slot labels. A matching name is a candidate, not a PID.
    intervals=sorted((e["start"],e["end"]) for e in record["mask_intervals"])
    merged=[]
    for a,b in intervals:
        require(0<=a<b<=len(text),"MARK_MASK_OFFSETS")
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    for e in record["entities"]:
        require(any(a<=e["start"]<e["end"]<=b for a,b in merged),"MARK_MASK_COVERAGE")
    parts=[];pos=0
    for a,b in merged:
        parts.append(text[pos:a])
        es=[e for e in record["entities"] if e["start"]<b and e["end"]>a]
        tag="정보"
        if len(es)==1 and es[0]["label"]=="person":
            e=es[0];n=canonicalize("person",text[e["start"]:e["end"]])
            slots=[i for i,q in enumerate(names,1) if n==q or suffix(n,q)]
            tag="검색인물"+str(slots[0]) if len(slots)==1 else "인물"
        elif len(es)==1:tag={"address":"주소","phone_number":"전화번호","email":"이메일","rrn":"주민번호","account_number":"계좌번호","card_number":"카드번호"}.get(es[0]["label"],"정보")
        parts.append(" "+tag+" ");pos=b
    parts.append(text[pos:])
    return " ".join("".join(parts).split())

class LocalCE:
    def __init__(self,batch,threads):
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        self.torch=torch;self.batch=batch;self.pairs=0;self.truncated=0;self.seconds=0
        torch.set_num_threads(threads)
        folder=snapshot_download(RR_REPO,revision=RR_REV,token=False,local_files_only=True)
        require((Path(folder)/"model.safetensors").is_file(),"RR_SAFE_WEIGHTS_MISSING")
        self.tokenizer=AutoTokenizer.from_pretrained(folder,local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForSequenceClassification.from_pretrained(folder,local_files_only=True,
                       trust_remote_code=False,use_safetensors=True,torch_dtype=torch.float32).eval()
        require(self.model.config.num_labels==1,"RR_NUM_LABELS")
        emit("RR_MODEL_READY",repo=RR_REPO,revision=RR_REV,device="cpu")
    def score(self,pairs):
        if not pairs:return []
        t=time.perf_counter();out=[]
        with self.torch.inference_mode():
            for i in range(0,len(pairs),self.batch):
                p=pairs[i:i+self.batch];q=[x[0] for x in p];d=[x[1] for x in p]
                raw=self.tokenizer(q,d,truncation=False,verbose=False)
                self.truncated+=sum(len(x)>512 for x in raw["input_ids"])
                z=self.tokenizer(q,d,padding=True,truncation="only_second",max_length=512,return_tensors="pt")
                scores=self.model(**z).logits.reshape(-1).tolist()
                require(len(scores)==len(p) and all(math.isfinite(v) for v in scores),"RR_INVALID_SCORES")
                out.extend(scores)
        self.pairs+=len(pairs);self.seconds+=time.perf_counter()-t
        return out

def rerank_modes(base,ce,plain_q,marked_q,plain_docs,marked_docs,conditions,route,k,has_text):
    # Preserve high performing exact identifier/address routes. No qid or
    # Gold based routing. Compare all variants with same top-k prefix.
    prefix=base[:k];tail=base[k:];pos={d:i for i,d in enumerate(base)}
    if route not in ("name","semantic","mixed") or not has_text or len(prefix)<2:
        return {m:list(base) for m in ("RR1_plain","RR2_condition","RR3_marked","RR4_fused")}
    ps=dict(zip(prefix,ce.score([(plain_q,plain_docs[d]) for d in prefix])))
    ms=dict(zip(prefix,ce.score([(marked_q,marked_docs[d]) for d in prefix]))) if route in ("name","mixed") else ps
    def sort(s,condition):
        return sorted(prefix,key=lambda d:((tuple(-v for v in conditions(d)) if condition else ()), -s[d],pos[d]))+tail
    # Rank fusion within original prefix; equal scores receive equal rank.
    f=equal_rank_rrf(prefix,[(ps,1,False),(ms,1,False),({d:-pos[d] for d in prefix},1,False)]) if route!="semantic" else equal_rank_rrf(prefix,[(ps,1,False),({d:-pos[d] for d in prefix},1,False)])
    return {"RR1_plain":sort(ps,False),"RR2_condition":sort(ps,True),
            "RR3_marked":sort(ms,True),"RR4_fused":sort(f,True)}

def rr_self_test():
    class Mock:
        def score(self,p):return [1.0 if x[1]=="b" else 0.0 for x in p]
    v=rerank_modes(["a","b","c"],Mock(),"q","q",{"a":"a","b":"b"},{"a":"a","b":"b"},lambda d:(int(d=="a"),),"name",2,True)
    assert v["RR1_plain"]==["b","a","c"] and v["RR2_condition"]==["a","b","c"]
    s=marked_view("가상민 전화",{"entities":[{"start":0,"end":3,"label":"person"}],"mask_intervals":[{"start":0,"end":3}]},["가상민"])
    assert s=="검색인물1 전화" and "가상민" not in s
    emit("RR_SELF_TEST",passed=2)



def mention_view(text,record,names,query_terms,context=False):
    # Find boundaries on the strictly masked character buffer so punctuation
    # inside protected values cannot split the span. Never pass raw spans.
    safe=list(text)
    for e in record["mask_intervals"]:
        a,b=e["start"],e["end"]
        require(0<=a<b<=len(text),"MR_MASK")
        safe[a:b]=[" "]*(b-a)
    protected="".join(safe)
    cuts=[0]+[m.end() for m in re.finditer(r"\n+|[.!?。！？](?=\s|$)",protected)]+[len(text)]
    cuts=sorted(set(cuts))
    spans=[(a,b) for a,b in zip(cuts,cuts[1:]) if protected[a:b].strip() or
           any(e["start"]<b and e["end"]>a for e in record["entities"])]
    hits=[]
    for e in record["entities"]:
        if e["label"]!="person":continue
        n=canonicalize("person",text[e["start"]:e["end"]])
        slots={i for i,q in enumerate(names) if n==q or suffix(n,q)}
        if slots:hits.append((e["start"],e["end"],slots))
    if not hits:return None
    candidates=[]
    for i,(a,b) in enumerate(spans):
        if not any(a<=x<y<=b for x,y,_ in hits):continue
        if context:
            # Adjacent segment on either side, but do not cross a blank
            # paragraph separator. Context can include other people.
            if i>0 and "\n\n" not in protected[spans[i-1][0]:a]:a=spans[i-1][0]
            if i+1<len(spans) and "\n\n" not in protected[b:spans[i+1][1]]:b=spans[i+1][1]
        slots=set().union(*(s for x,y,s in hits if a<=x<y<=b))
        # Boundaries must not bisect any strict protection interval.
        for e in record["mask_intervals"]:
            if e["start"]<a<e["end"]:a=e["start"]
            if e["start"]<b<e["end"]:b=e["end"]
        local={"mask_intervals":[{"start":max(a,e["start"])-a,"end":min(b,e["end"])-a}
               for e in record["mask_intervals"] if e["start"]<b and e["end"]>a],
               "entities":[{**e,"start":e["start"]-a,"end":e["end"]-a}
               for e in record["entities"] if a<=e["start"]<e["end"]<=b]}
        v=marked_view(text[a:b],local,names)
        # Use remaining non-PII words/grams, not candidate markers, for
        # deterministic window selection. This is not an event extractor.
        t=terms(text[a:b],local["mask_intervals"])
        overlap_count=len(set(query_terms)&set(t))
        candidates.append((len(slots),overlap_count,-len(v),-a,v))
    return max(candidates)[-1] if candidates else None

def window_ranking(base,reference,ws,conditions,k):
    # Preserve the exact original candidate set and tail. Missing windows
    # do not receive a fabricated CE score; retain reference ordering there.
    prefix=base[:k];tail=base[k:];pos={d:i for i,d in enumerate(reference)}
    ref={d:-pos[d] for d in prefix}
    f=equal_rank_rrf(prefix,[(ref,1,False),(ws,1,False)])
    return sorted(prefix,key=lambda d:(tuple(-x for x in conditions(d)),
                 -f[d],pos[d]))+tail

class MemoCE:
    """Private score-only cache. Never store text, names, tokens or keys."""
    def __init__(self,backend,path):
        import hashlib
        self.hashlib=hashlib;self.backend=backend;self.path=path
        self.cache={};self.requests=0;self.hits=0
        if path.exists():
            for line in path.read_text().splitlines():
                key,value=json.loads(line)
                require(isinstance(key,str) and len(key)==64 and math.isfinite(value),"MR_CACHE_FORMAT")
                self.cache[key]=float(value)
        emit("MR_CACHE",entries=len(self.cache))
    def score(self,pairs):
        keys=[self.hashlib.sha256(json.dumps([RR_REPO,RR_REV,512,"float32-v1",q,d],
              ensure_ascii=False,separators=(",",":")).encode()).hexdigest() for q,d in pairs]
        self.requests+=len(keys)
        missing={}
        for key,pair in zip(keys,pairs):
            if key in self.cache:self.hits+=1
            elif key not in missing:missing[key]=pair
        if missing:
            values=self.backend.score(list(missing.values()))
            require(len(values)==len(missing),"MR_CACHE_LENGTH")
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open("a",encoding="utf-8") as f:
                os.chmod(self.path,0o600)
                for key,value in zip(missing,values):
                    self.cache[key]=value;f.write(json.dumps([key,value])+"\n")
        return [self.cache[key] for key in keys]
    @property
    def pairs(self):return self.backend.pairs
    @property
    def seconds(self):return self.backend.seconds
    @property
    def truncated(self):return self.backend.truncated

def mr_self_test():
    t="가상민 카드 변경. 나가람 신규 발급."
    a=t.index("나가람")
    r={"mask_intervals":[{"start":0,"end":3},{"start":a,"end":a+3}],
       "entities":[{"start":0,"end":3,"label":"person"},{"start":a,"end":a+3,"label":"person"}]}
    v=mention_view(t,r,["가상민"],terms("신규 발급",[]))
    assert v and "검색인물1" in v and "가상민" not in v and "나가람" not in v and "신규" not in v
    assert mention_view(t,r,["최가상"],{}) is None
    v=mention_view(t,r,["가상민"],{},True)
    assert v and "나가람" not in v and "신규" in v
    b=["a","b","c"];rank=window_ranking(b,b,{"b":10,"a":1},lambda d:(int(d=="a"),),2)
    assert rank==b
    emit("MR_SELF_TEST",passed=4)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("document-cache","query-cache","reference","controller-report"):p.add_argument("--"+n,type=Path,required=True)
    p.add_argument("--fetch-team",action="store_true")
    p.add_argument("--models",nargs="+",choices=["bge"],default=["bge"])
    p.add_argument("--batch",type=int,default=4);p.add_argument("--threads",type=int,default=4)
    p.add_argument("--skip-kiwi",action="store_true")
    p.add_argument("--rerank-k",type=int,default=8)
    p.add_argument("--score-cache",type=Path,required=True)
    a=p.parse_args();require(a.batch>0 and a.threads>0 and len(set(a.models))==len(a.models),"ARGUMENTS")
    os.environ["HF_HUB_DISABLE_TELEMETRY"]="1";os.environ["TOKENIZERS_PARALLELISM"]="false"
    require(2<=a.rerank_k<=20,"RERANK_K");self_test();adaptive_self_test();rr_self_test();mr_self_test();own=team_source(a.fetch_team)
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    qs=queries(qp);gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    gm={g["qid"]:g for g in gold}
    dc=json.loads(a.document_cache.expanduser().read_text());qc=json.loads(a.query_cache.expanduser().read_text());final=json.loads(a.controller_report.expanduser().read_text())
    m=read_manifest(Path.cwd()/"models/v7_model_release.json")
    require(qc["query_sha256"]==sha(qp) and qc["query_gold"]["sha256"]==sha(gp) and qc["policy"]==V7_POLICY and qc["model_revision"]==m["revision"]==final["model_revision"],"QUERY_CACHE_MISMATCH")
    cm={r["qid"]:r for r in qc["private_query_offsets"]};dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]}
    require(len(cm)==len(gm)==len(qs)==88 and set(cm)==set(gm)=={q["qid"] for q in qs},"QUERY_IDS")
    changes={x["qid"]:x for x in final["details"]}
    require(final["external_transfer_approved"] is False and final["counts"]["outputs_original_mask_preserved"]==88 and len(changes)==2,"CONTROLLER_REPLAY")
    rows=corpus(a.document_cache.expanduser(),a.reference.expanduser())
    secret=secrets.token_bytes(32);nodes=[];wide_nodes=[];truth={};doc_pids={};index=defaultdict(set);regions=defaultdict(set);docs={};masked_docs={};raw_docs={}
    for did,_,text,records,pids,ref in rows:
        # Re-key documents and queries together; never persist or print keys.
        for e in records:
            e["token"],e["normalized"]=make_hmac_token(secret,e["label"],text[e["start"]:e["end"]])
            index[e["token"]].add(did)
        for k in document_region_keys(text,records,secret):regions[k].add(did)
        mentions,legacy,old,modes,strict,wide=refine(text,records,own)
        nodes.extend(nodes_for(did,mentions,strict,records));wide_nodes.extend(nodes_for(did,mentions,wide,records))
        truth.update({(did,*s):pid for s,pid in pids.items()});doc_pids[did]=set(pids.values())
        docs[did]=terms(text,dm[did]["mask_intervals"])
        masked_docs[did]=masked(text,dm[did]);raw_docs[did]=text
    require(len(docs)==184 and len(nodes)==len(wide_nodes)==len(truth)==551,"INDEX_PARITY")
    by_id={n["id"]:i for i,n in enumerate(nodes)};require(all(x["id"]==y["id"] for x,y in zip(nodes,wide_nodes)),"NODE_PARITY")
    links=full_snapshot(nodes);adj=defaultdict(set)
    for x,y in links:
        i,j=by_id[x],by_id[y];adj[i].add(j);adj[j].add(i)
    by_name=defaultdict(set)
    for i,n in enumerate(nodes):by_name[n["name"]].add(i)
    lexical=Lexical(docs);ready=set(docs)
    emit("IR_INPUT",ready_docs=len(ready),mentions=len(nodes),experimental_graph_edges=len(links),queries=len(qs),scope="local full evaluation access; no live ACL or answerability")
    masked_queries={q["qid"]:masked(q["text"],cm[q["qid"]]) for q in qs}
    kiwi=None;kiwi_index=None
    if not a.skip_kiwi:
        from kiwipiepy import Kiwi
        kiwi=Kiwi(num_workers=1)
        kiwi_index=Lexical({d:kiwi_terms(kiwi,t) for d,t in masked_docs.items()})
        emit("DR_KIWI_READY",documents=len(masked_docs))
    dense={}
    for model_name in a.models:
        dense[model_name]=dense_scores(model_name,masked_docs,masked_queries,a.batch,a.threads)
    ce=MemoCE(LocalCE(a.batch,a.threads),a.score_cache.expanduser())
    totals=defaultdict(Counter);nq=Counter();ts=[];gold_candidate=Counter();case_results=[]
    for q in qs:
        t0=time.perf_counter();qid=q["qid"];text=q["text"];r=cm[qid];g=gm[qid]
        require(r["length"]==len(text) and g.get("query",text)==text,"QUERY_TEXT")
        typed=list(r["entities"]);change=changes.get(qid)
        if change:
            if change["reason"]=="numeric_candidate":
                require(r["decision"]==BLOCKED,"NUMERIC_BASELINE:"+qid)
                ps=plans(text,typed,r["review_events"],key=secret,lookup=index.__contains__)
                chosen=[x for x in ps if x.status=="CANDIDATE"]
                require(bool(chosen) and not any(x.status=="CLARIFY" for x in ps),"NUMERIC_REPLAY")
                typed=[e for e in typed if not any(overlap(x.start,x.end,e) for x in chosen)]
                typed=sorted(typed+[dict(start=x.start,end=x.end,label=x.label) for x in chosen],key=lambda e:e["start"])
            elif change["reason"]=="region_candidate":require(r["decision"]==REVIEW and not typed and bool(r["review_events"]),"REGION_REPLAY:"+qid)
            else:raise RuntimeError("UNKNOWN_RECOVERY")
        else:require(r["decision"]==READY,"QUERY_READY:"+qid+":"+str(r["decision"]))
        strict=rebuild(text,typed,r["mask_intervals"],secret)
        proj=project(text,strict,r,key=secret,query=True,region_lookup=regions.__contains__)
        require(proj.external_transfer_approved is False,"EGRESS")
        lex=lexical.score(terms(text,r["mask_intervals"]))
        lex_rank=ranked({d for d,s in lex.items() if s>0},lex,{})
        qnames=[];qattrs=[]
        token_scores=Counter()
        for e in strict.safe_token_manifest:
            for d in index.get(e["token"],()):token_scores[d]+=1
            if e["label"]=="person":
                n=canonicalize("person",text[e["start"]:e["end"]]);qnames.append((n,role_of(own,text,e)))
            else:qattrs.append((e["label"],e["token"]))
        for k in proj.region_keys:
            for d in regions[k]:token_scores[d]+=1
        tok_rank=ranked(set(token_scores),token_scores,lex)
        ranks={"R_lexical":lex_rank,"R_token":tok_rank,"R_token_lexical_rrf":rrf(tok_rank,lex_rank)}
        # For detected names, candidate sets and role-aware rank. No arbitrary
        # first candidate or weak-link promotion; graph expands only supported
        # query anchors or role-compatible seeds, which remain tentative.
        exact=set().union(*(by_name[n] for n,_ in qnames))
        alias={i for i,n in enumerate(nodes) if any(suffix(n["name"],x) for x,_ in qnames)}-exact
        broad=exact|alias
        known_roles={role for _,role in qnames if role!=UNKNOWN}
        def role_match(i):
            return any((nodes[i]["name"]==name or suffix(nodes[i]["name"],name)) and role!=UNKNOWN and nodes[i]["role"]==role for name,role in qnames)
        def anchors(i,ns):
            return sum(tok in ns[i]["attrs"].get(lab,set()) for lab,tok in qattrs)
        role_seeds={i for i in broad if role_match(i)}
        strict_seeds={i for i in broad if anchors(i,nodes)>0}
        expanded=set().union(*(adj[i] for i in strict_seeds|role_seeds))
        linked=broad|expanded
        def scores(ids,mode):
            out=defaultdict(float)
            for i in ids:
                n=nodes[i];v=2.0 if i in exact else 1.0
                if mode>=2:v+=2.0*anchors(i,nodes)+(.5 if i in expanded else 0)
                if mode>=3:v+=.5*max(0,anchors(i,wide_nodes)-anchors(i,nodes))
                if mode>=4:v+=1.0*role_match(i)
                if mode==5:v= (2.0 if i in exact else 1.0)+1.0*role_match(i)
                out[n["doc"]]=max(out[n["doc"]],v)
            return out
        ids_for={}
        if qnames:
            for mode,ids,k in (("IR0_exact",exact,0),("IR1_alias",broad,1),("IR2_graph",linked,2),("IR3_candidate_attributes",linked,3),("IR4_role",linked,4),("IR4_role_only_control",broad,5)):
                s=scores(ids,k);ranks[mode]=ranked(set(s),s,lex);ids_for[mode]=ids
            ranks["IR5_local_fusion"]=rrf(ranks["IR4_role"],tok_rank,lex_rank);ids_for["IR5_local_fusion"]=linked
        # Additional retrieval modes. The same strict masked representation
        # is used for Kiwi and every dense model. No new Gold input.
        if kiwi_index is not None:
            kl=kiwi_index.score(kiwi_terms(kiwi,masked_queries[qid]))
            kr=ranked({d for d,s in kl.items() if s>0},kl,{})
            ranks["DR_kiwi"]=kr
            ranks["DR_token_kiwi_rrf"]=rrf(tok_rank,kr)
        else:kr=lex_rank;kl=lex
        name_sets=[];exact_sets=[]
        for name in dict.fromkeys(n for n,_ in qnames):
            exact_sets.append({nodes[i]["doc"] for i in by_name[name]})
            name_sets.append({n["doc"] for n in nodes if n["name"]==name or suffix(n["name"],name)})
        required=[set(index.get(tok,())) for lab,tok in dict.fromkeys(qattrs)]
        region_sets=[set(regions[k]) for k in proj.region_keys]
        for model_name,matrix in dense.items():
            ds=matrix[qid]
            dr=ranked(set(ds),ds,{})
            ranks["DR_"+model_name+"_dense20"]=dr[:20]
            # Fixed candidate budgets, then two generic condition policies.
            # Avoid double voting from token and identity rankings.
            sr=fusion([(dr,2),(kr,1)],20)
            hr=fusion([(dr,1),(kr,1),(tok_rank,1)],20)
            ranks["DR_"+model_name+"_rrf20"]=sorted(hr,key=lambda d:(-hr[d],d))
            tier,soft=condition_ranks(sr,name_sets,required,region_sets,exact_sets)
            ranks["DR_"+model_name+"_tier"]=tier
            ranks["DR_"+model_name+"_soft"]=soft
            extra,route,route_info=adaptive_modes(ds,kl,lex,dr,kr,lex_rank,name_sets,required,region_sets,exact_sets,qattrs,qnames,nodes,wide_nodes,broad,bool(terms(text,r["mask_intervals"])))
            require(extra["AR0_previous_tier"]==tier,"AR0_PARITY:"+qid)
            ranks.update(extra)
            base=extra["AR0_previous_tier"]
            names=list(dict.fromkeys(n for n,_ in qnames))
            marked_q=marked_view(text,r,names)
            marked_docs={d:marked_view(raw_docs[d],dm[d],names) for d in base[:a.rerank_k]}
            conditions=lambda d:(cov(d,required),cov(d,region_sets),cov(d,name_sets))
            before=ce.pairs
            rr=rerank_modes(base,ce,masked_queries[qid],marked_q,masked_docs,marked_docs,conditions,route,a.rerank_k,route_info["semantic_text"])
            ranks={"RR0_baseline":base,**rr}
            mr={m:list(rr["RR4_fused"]) for m in ("MR1_sentence","MR2_context","MR3_context_fused","MR4_context_top4")}
            window_pairs=0;before_window=ce.pairs;window_t=time.perf_counter()
            if route in ("name","mixed") and route_info["semantic_text"]:
                prefix=base[:a.rerank_k]
                qt=terms(text,r["mask_intervals"])
                def get_scores(context):
                    nonlocal window_pairs
                    pairs=[];ids=[]
                    for d in prefix:
                        v=mention_view(raw_docs[d],dm[d],names,qt,context)
                        if v is not None:
                            ids.append(d);pairs.append((marked_q,v))
                    window_pairs+=len(pairs)
                    return dict(zip(ids,ce.score(pairs)))
                ss=get_scores(False);cs=get_scores(True)
                mr["MR1_sentence"]=window_ranking(base,base,ss,conditions,a.rerank_k)
                mr["MR2_context"]=window_ranking(base,base,cs,conditions,a.rerank_k)
                mr["MR3_context_fused"]=window_ranking(base,rr["RR4_fused"],cs,conditions,a.rerank_k)
                mr["MR4_context_top4"]=window_ranking(base,base,cs,conditions,min(4,a.rerank_k))
            ranks.update(mr)
            emit("MR_QUERY",qid=qid,route=route,window_pairs=window_pairs,
                 actual_new_window_pairs=ce.pairs-before_window,window_ms=(time.perf_counter()-window_t)*1000)
            for mode,rank in list(ranks.items()):
                require(set(rank)==set(base) and len(rank)==len(base),"RR_POOL_PARITY")
                active=required+name_sets
                if not active:active=region_sets
                shortlist=[d for d in rank if d in active[0] or (not name_sets and any(d in s for s in region_sets))][:5] if len(active)==1 else rank[:5]
                ranks[mode+"_shortlist"]=shortlist
            emit("RR_PROGRESS",qid=qid,route=route,pairs=ce.pairs-before,total_pairs=ce.pairs)
        # Evaluation starts here: Gold never decides rank or seeds.
        target_items=[e for e in g["query_entities"] if isinstance(e,dict) and e.get("label")=="person"]
        targets={e["person_id"] for e in target_items}
        rel=set(g["relevant_doc_ids"]);eligible=rel&ready;conf=set(g.get("confusable_doc_ids",[]))&ready
        groups=["all88"]+ (["name55"] if targets else ["nonname33"])
        ambiguous=any(len({truth[m] for i in by_name[canonicalize("person",e["text"])] for m in nodes[i]["mentions"]})>1 for e in target_items)
        if ambiguous:groups.append("ambiguous12")
        groups.append("route_"+route)
        if targets:
            # Gold span oracle is a separate candidate-only ceiling, not a mode.
            gn={canonicalize("person",e["text"]) for e in target_items}
            gs={i for i,n in enumerate(nodes) if n["name"] in gn or any(suffix(n["name"],x) for x in gn)}
            matched=defaultdict(set)
            for i in gs:matched[nodes[i]["doc"]].update(truth[m] for m in nodes[i]["mentions"])
            gold_candidate["relevant"]+=len(eligible);gold_candidate["matched"]+=sum(targets<=matched[d] for d in eligible)
        for mode,rank in ranks.items():
            matched=defaultdict(set)
            for i in ids_for.get(mode,broad):
                matched[nodes[i]["doc"]].update(truth[m] for m in nodes[i]["mentions"])
            v=evaluate(rank,rel,conf,targets,matched,doc_pids)
            v["ready_relevant"]=len(eligible);v["ready_rel_candidates"]=len(set(rank)&eligible)
            v["top5_docs"]=len(rank[:5]);v["relevant5"]=len(set(rank[:5])&rel)
            v["hit3"]=int(bool(set(rank[:3])&rel));v["top3_docs"]=len(rank[:3]);v["relevant3"]=len(set(rank[:3])&rel)
            v["confusable3"]=len(set(rank[:3])&conf)
            v["returned_query"]=int(bool(rank));v["returned_without_relevant"]=int(bool(rank) and not v["hit5"])
            if targets:
                v["wrong_name_matched5"]=sum(not targets<=doc_pids.get(d,set()) and bool(matched.get(d,set())-targets) for d in rank[:5])
                v["target_missing_other5"]=v["wrong_person_docs5"]-v["wrong_name_matched5"]
            v["confusable_before_first_relevant"]=sum(d in conf for d in rank[:next((i for i,d in enumerate(rank[:5]) if d in rel),5)])
            v["queries_confusable5"]=int(bool(set(rank[:5])&conf))
            v["ready_query"]=int(bool(eligible))
            v["ready_hit1"]=v["hit1"] if eligible else 0
            v["ready_hit5"]=v["hit5"] if eligible else 0
            v["ready_rr"]=v["rr"] if eligible else 0
            if mode.startswith(("RR","MR")):
                case_results.append(dict(qid=qid,mode=mode,**v))
            for group in groups:
                totals[mode,group].update(v);nq[mode,group]+=1
        ts.append((time.perf_counter()-t0)*1000)
    require(nq["RR0_baseline","all88"]==88 and nq["RR0_baseline","name55"]==55 and nq["RR0_baseline","ambiguous12"]==12,"RR_GROUP_PARITY")
    require(totals["RR0_baseline","all88"]["hit1"]==78 and totals["RR0_baseline","all88"]["hit5"]==87,"RR_BASELINE_PARITY")
    require(all(nq[mode,"all88"]==88 for mode in ("RR1_plain","RR2_condition","RR3_marked","RR4_fused","RR0_baseline_shortlist","RR1_plain_shortlist","RR2_condition_shortlist","RR3_marked_shortlist","RR4_fused_shortlist")),"RR_MODE_PARITY")
    for (mode,group),c in sorted(totals.items()):
        n=nq[mode,group]
        if not mode.startswith(("RR","MR")):continue
        emit("MR_RESULT",hit3=c["hit3"]/n,precision3=c["relevant3"]/c["top3_docs"] if c["top3_docs"] else None,mode=mode,group=group,queries=n,returned_coverage=c["returned_query"]/n,mean_returned_top5=c["top5_docs"]/n,hit1=c["hit1"]/n,hit5=c["hit5"]/n,mrr=c["rr"]/n,
             ready_hit1=c["ready_hit1"]/c["ready_query"] if c["ready_query"] else None,
             ready_hit5=c["ready_hit5"]/c["ready_query"] if c["ready_query"] else None,
             ready_mrr=c["ready_rr"]/c["ready_query"] if c["ready_query"] else None,
             precision5=c["relevant5"]/c["top5_docs"] if c["top5_docs"] else None,
             wrong_target_fraction5=c["wrong_person_docs5"]/c["top5_docs"] if group in ("name55","ambiguous12") and c["top5_docs"] else None,
             macro_recall5=c["recall5"]/n,candidate_recall=c["rel_candidates"]/c["rel_total"] if c["rel_total"] else None,
             ready_candidate_recall=c["ready_rel_candidates"]/c["ready_relevant"] if c["ready_relevant"] else None,
             target_relevant_candidate_recall=c["target_rel_candidates"]/c["ready_relevant"] if group in ("name55","ambiguous12") else None,
             counts=dict(c))
    require(gold_candidate["relevant"]==147 and gold_candidate["matched"]==147,"GOLD_ALIAS_ORACLE_PARITY")
    for v in case_results:
        emit("MR_CASE",**v)
    require(all(nq[m,"all88"]==88 for m in ("MR1_sentence","MR2_context","MR3_context_fused","MR4_context_top4")),"MR_MODE_PARITY")
    emit("MR_COMPLETE",ce_pairs=ce.pairs,ce_requests=ce.requests,ce_cache_hits=ce.hits,ce_seconds=ce.seconds,ce_truncated=ce.truncated,gold_span_alias_oracle=dict(gold_candidate),models=a.models,mean_cached_ranking_ms=sum(ts)/len(ts),
         scope="development only; CPU cross-encoder, no actual ACL, answerability or HCX; no Holdout2")
if __name__=="__main__":main()
