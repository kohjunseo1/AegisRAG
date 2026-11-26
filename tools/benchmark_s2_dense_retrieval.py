"""Local dense / lexical / condition-aware retrieval ablation.
Frozen V7 offsets, O4 and Q88 controller replay. No external inference.
Gold only scores. All models run sequentially, one strict masked text view,
512-token shared window (long inputs counted), no identity authorization.
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
    from huggingface_hub import HfApi,snapshot_download
    from transformers import AutoTokenizer,AutoModel
    torch.set_num_threads(threads)
    model_id=MODELS[name]
    api=HfApi()
    # BGE-M3 main publishes pickle weights only at the observed revision.
    # Use the same-repository SFconvertbot safetensors conversion, pinned
    # by commit; verify that its original weights/config/tokenizer match main.
    if name=="bge":
        original=api.model_info(model_id,revision="5617a9f61b028005a4858fdac845db406aefb181",files_metadata=True,token=False)
        converted=api.model_info(model_id,revision="9a0624b896d81da7492a910ffa53731274b6cf3d",files_metadata=True,token=False)
        orig={f.rfilename:f for f in original.siblings}
        conv={f.rfilename:f for f in converted.siblings}
        for file in ("pytorch_model.bin","config.json","tokenizer.json","tokenizer_config.json","sentencepiece.bpe.model","1_Pooling/config.json"):
            require(file in orig and file in conv,"BGE_CONVERSION_SOURCE_MISSING:"+file)
            x,y=orig[file],conv[file]
            require(bool(x.blob_id) and x.blob_id==y.blob_id,"BGE_CONVERSION_SOURCE_MISMATCH:"+file)
        require("model.safetensors" in conv,"BGE_SAFETENSORS_MISSING")
        rev=converted.sha
        emit("DR_BGE_CONVERSION",original_revision=original.sha,conversion_revision=rev,source_files_match=True)
    else:
        rev=api.model_info(model_id,token=False).sha
    require(bool(rev),"MODEL_REVISION")
    emit("DR_MODEL_START",model=name,repo=model_id,revision=rev,device="cpu",batch=batch)
    t0=time.perf_counter()
    folder=Path(snapshot_download(model_id,revision=rev,token=False,
        allow_patterns=["*.json","*.safetensors","*.model","*.txt","*.vocab"],
        ignore_patterns=["onnx/*","openvino/*","**/onnx/*","**/openvino/*","**/optimizer*"]))
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


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("document-cache","query-cache","reference","controller-report"):p.add_argument("--"+n,type=Path,required=True)
    p.add_argument("--fetch-team",action="store_true")
    p.add_argument("--models",nargs="+",choices=list(MODELS),default=list(MODELS))
    p.add_argument("--batch",type=int,default=4);p.add_argument("--threads",type=int,default=4)
    p.add_argument("--skip-kiwi",action="store_true")
    a=p.parse_args();require(a.batch>0 and a.threads>0 and len(set(a.models))==len(a.models),"ARGUMENTS")
    os.environ["HF_HUB_DISABLE_TELEMETRY"]="1";os.environ["TOKENIZERS_PARALLELISM"]="false"
    self_test();own=team_source(a.fetch_team)
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
    secret=secrets.token_bytes(32);nodes=[];wide_nodes=[];truth={};doc_pids={};index=defaultdict(set);regions=defaultdict(set);docs={};masked_docs={}
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
        masked_docs[did]=masked(text,dm[did])
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
        else:kr=lex_rank
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
        # Evaluation starts here: Gold never decides rank or seeds.
        target_items=[e for e in g["query_entities"] if isinstance(e,dict) and e.get("label")=="person"]
        targets={e["person_id"] for e in target_items}
        rel=set(g["relevant_doc_ids"]);eligible=rel&ready;conf=set(g.get("confusable_doc_ids",[]))&ready
        groups=["all88"]+ (["name55"] if targets else ["nonname33"])
        ambiguous=any(len({truth[m] for i in by_name[canonicalize("person",e["text"])] for m in nodes[i]["mentions"]})>1 for e in target_items)
        if ambiguous:groups.append("ambiguous12")
        if targets:
            # Gold span oracle is a separate candidate-only ceiling, not a mode.
            gn={canonicalize("person",e["text"]) for e in target_items}
            gs={i for i,n in enumerate(nodes) if n["name"] in gn or any(suffix(n["name"],x) for x in gn)}
            matched=defaultdict(set)
            for i in gs:matched[nodes[i]["doc"]].update(truth[m] for m in nodes[i]["mentions"])
            gold_candidate["relevant"]+=len(eligible);gold_candidate["matched"]+=sum(targets<=matched[d] for d in eligible)
        for mode in ("IR0_exact","IR1_alias","IR2_graph","IR3_candidate_attributes","IR4_role","IR4_role_only_control","IR5_local_fusion"):
            if mode not in ranks:
                # Report all Q88 without silently excluding missed name detections.
                ranks[mode]=ranks["R_token_lexical_rrf"] if mode=="IR5_local_fusion" else tok_rank
                ids_for[mode]=set()
        for mode,rank in ranks.items():
            matched=defaultdict(set)
            for i in ids_for.get(mode,broad):
                matched[nodes[i]["doc"]].update(truth[m] for m in nodes[i]["mentions"])
            v=evaluate(rank,rel,conf,targets,matched,doc_pids)
            v["ready_relevant"]=len(eligible);v["ready_rel_candidates"]=len(set(rank)&eligible)
            v["top5_docs"]=len(rank[:5]);v["relevant5"]=len(set(rank[:5])&rel)
            v["confusable_before_first_relevant"]=sum(d in conf for d in rank[:next((i for i,d in enumerate(rank[:5]) if d in rel),5)])
            v["queries_confusable5"]=int(bool(set(rank[:5])&conf))
            v["ready_query"]=int(bool(eligible))
            v["ready_hit1"]=v["hit1"] if eligible else 0
            v["ready_hit5"]=v["hit5"] if eligible else 0
            v["ready_rr"]=v["rr"] if eligible else 0
            if mode.startswith("DR") or mode in ("R_token","R_token_lexical_rrf","IR1_alias","IR5_local_fusion"):
                case_results.append(dict(qid=qid,mode=mode,**v))
            for group in groups:
                totals[mode,group].update(v);nq[mode,group]+=1
        ts.append((time.perf_counter()-t0)*1000)
    require(nq["IR0_exact","name55"]==55 and nq["IR0_exact","ambiguous12"]==12,"Q55_PARITY")
    for (mode,group),c in sorted(totals.items()):
        n=nq[mode,group]
        emit("DR_RESULT",mode=mode,group=group,queries=n,hit1=c["hit1"]/n,hit5=c["hit5"]/n,mrr=c["rr"]/n,
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
        if not v["hit1"] or v.get("wrong_person_docs5",0) or v["confusable5"]:
            emit("DR_CASE",**v)
    emit("DR_COMPLETE",gold_span_alias_oracle=dict(gold_candidate),models=a.models,mean_cached_ranking_ms=sum(ts)/len(ts),
         scope="development only; same strict masks, CPU dense vectors, no actual ACL, reranker, answerability or HCX; no Holdout2")
if __name__=="__main__":main()
