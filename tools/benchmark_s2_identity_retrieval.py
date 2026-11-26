"""First local identity-aware retrieval ablation, frozen O4 + cached V7.
Q55 name queries (detected spans, NOT Gold). Full Q88 token/lexical baseline
is separate, using prior verified controller replay. No embedding/DB/LLM.
Gold relevance and person IDs are scoring only. No disclosure authorization.
"""
import argparse,json,math,re,secrets,time
from collections import Counter,defaultdict
from pathlib import Path
from tools.benchmark_s2_ownership_o4 import refine,nodes_for
from tools.benchmark_s2_ownership_o3 import corpus
from tools.benchmark_s2_identity_graph import team_source,require,emit,sha,suffix
from tools.benchmark_s2_identity_incremental import full_snapshot
from tools.audit_s2_query_identity_evidence import role_of
from tools.benchmark_s2_prepare import queries
from tools.inspect_s2_protection_private import rebuild,overlap
from starter.s2_pseudonymizer import V7_POLICY,make_hmac_token,canonicalize
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

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("document-cache","query-cache","reference","controller-report"):p.add_argument("--"+n,type=Path,required=True)
    p.add_argument("--fetch-team",action="store_true");a=p.parse_args();own=team_source(a.fetch_team)
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
    secret=secrets.token_bytes(32);nodes=[];wide_nodes=[];truth={};doc_pids={};index=defaultdict(set);regions=defaultdict(set);docs={}
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
    require(len(docs)==184 and len(nodes)==len(wide_nodes)==len(truth)==551,"INDEX_PARITY")
    by_id={n["id"]:i for i,n in enumerate(nodes)};require(all(x["id"]==y["id"] for x,y in zip(nodes,wide_nodes)),"NODE_PARITY")
    links=full_snapshot(nodes);adj=defaultdict(set)
    for x,y in links:
        i,j=by_id[x],by_id[y];adj[i].add(j);adj[j].add(i)
    by_name=defaultdict(set)
    for i,n in enumerate(nodes):by_name[n["name"]].add(i)
    lexical=Lexical(docs);ready=set(docs)
    emit("IR_INPUT",ready_docs=len(ready),mentions=len(nodes),experimental_graph_edges=len(links),queries=len(qs),scope="local full evaluation access; no live ACL or answerability")
    totals=defaultdict(Counter);nq=Counter();ts=[];gold_candidate=Counter()
    for q in qs:
        t0=time.perf_counter();qid=q["qid"];text=q["text"];r=cm[qid];g=gm[qid]
        require(r["length"]==len(text) and g.get("query",text)==text,"QUERY_TEXT")
        typed=list(r["entities"]);change=changes.get(qid)
        if change:
            if change["reason"]=="numeric_candidate":
                ps=plans(text,typed,r["review_events"],key=secret,lookup=index.__contains__)
                chosen=[x for x in ps if x.status=="CANDIDATE"]
                require(bool(chosen) and not any(x.status=="CLARIFY" for x in ps),"NUMERIC_REPLAY")
                typed=[e for e in typed if not any(overlap(x.start,x.end,e) for x in chosen)]
                typed=sorted(typed+[dict(start=x.start,end=x.end,label=x.label) for x in chosen],key=lambda e:e["start"])
            elif change["reason"]=="region_candidate":require(not typed and bool(r["review_events"]),"REGION_REPLAY")
            else:raise RuntimeError("UNKNOWN_RECOVERY")
        else:require(r["decision"]=="READY","QUERY_READY")
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
            for group in groups:
                totals[mode,group].update(v);nq[mode,group]+=1
            if targets and mode.startswith("IR"):
                emit("IR_CASE",qid=qid,mode=mode,**v,detected_name_spans=len(qnames),gold_name_spans=len(target_items),ambiguous=ambiguous,
                     strict_anchor_seeds=len(strict_seeds),role_seeds=len(role_seeds),graph_extra_mentions=len(linked-broad))
        ts.append((time.perf_counter()-t0)*1000)
    require(nq["IR0_exact","name55"]==55 and nq["IR0_exact","ambiguous12"]==12,"Q55_PARITY")
    for (mode,group),c in sorted(totals.items()):
        n=nq[mode,group]
        emit("IR_RESULT",mode=mode,group=group,queries=n,hit1=c["hit1"]/n,hit5=c["hit5"]/n,mrr=c["rr"]/n,
             macro_recall5=c["recall5"]/n,candidate_recall=c["rel_candidates"]/c["rel_total"] if c["rel_total"] else None,
             ready_candidate_recall=c["ready_rel_candidates"]/c["ready_relevant"] if c["ready_relevant"] else None,
             target_relevant_candidate_recall=c["target_rel_candidates"]/c["ready_relevant"] if group in ("name55","ambiguous12") else None,
             counts=dict(c))
    require(gold_candidate["relevant"]==147 and gold_candidate["matched"]==147,"GOLD_ALIAS_ORACLE_PARITY")
    emit("IR_COMPLETE",gold_span_alias_oracle=dict(gold_candidate),mean_combined_query_ms=sum(ts)/len(ts),scope="local lexical and candidate diagnostic; no dense retrieval, real ACL, evidence authorization, HCX or Holdout2")
if __name__=="__main__":main()
