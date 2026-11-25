"""Audit whether Q88 relevant documents require cross-document identity links.
Gold query spans and relevance are used for offline diagnosis only, not
inference or target-PID labels. No raw text, names, values, or IDs printed
except QIDs. No model, DB or LLM calls.
"""
import argparse,json
from collections import Counter,defaultdict
from itertools import combinations
from pathlib import Path
from tools.benchmark_s2_identity_graph import load,team_source,require,emit,sha,suffix,evidence
from tools.benchmark_s2_identity_incremental import full_snapshot,owners,shared_keys
from tools.benchmark_s2_prepare import queries
from tools.audit_s2_query_gold import parse_gold
from starter.s2_pseudonymizer import canonicalize,V7_POLICY
from starter.v7_model_store import read_manifest

def role_of(ownership,text,e):
    a,b=e["start"],e["end"]
    left=text.rfind("\n",0,a)+1;right=text.find("\n",b)
    return ownership.role_for(text,e,left,len(text) if right<0 else right)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",type=Path,required=True)
    p.add_argument("--query-cache",type=Path,required=True)
    p.add_argument("--fetch-team",action="store_true")
    a=p.parse_args()
    own=team_source(a.fetch_team);nodes,truth=load(a.document_cache,own)
    root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    qp=root/"holdout_retrieval_queries.jsonl";gp=root/"holdout_retrieval_queries_gold.jsonl"
    qs=queries(qp);gold=[json.loads(s) for s in gp.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    gm={g["qid"]:g for g in gold}
    cache=json.loads(a.query_cache.expanduser().read_text())
    manifest=read_manifest(Path.cwd()/"models/v7_model_release.json")
    require(cache["policy"]==V7_POLICY and cache["model_revision"]==manifest["revision"]
        and cache["query_sha256"]==sha(qp) and cache["query_gold"]["sha256"]==sha(gp),"QUERY_CACHE_MISMATCH")
    cm={r["qid"]:r for r in cache["private_query_offsets"]}
    require(len(cm)==len(cache["private_query_offsets"])==len(gm)==len(gold)==len(qs)==88 and set(cm)==set(gm)=={q["qid"] for q in qs},"QUERY_IDS")
    dc=json.loads(a.document_cache.expanduser().read_text())
    from starter.s2_pseudonymizer import READY
    ready={str(r["doc_id"]) for r in dc["private_document_offsets"] if r["decision"]==READY}
    node_pids=[{truth[m] for m in n["mentions"]} for n in nodes]
    links=full_snapshot(nodes);shared=shared_keys(owners(nodes))
    by_name=defaultdict(set)
    for i,n in enumerate(nodes):by_name[n["name"]].add(i)
    all_counts=Counter();amb_counts=Counter()
    for q in qs:
        qid=q["qid"];text=q["text"];g=gm[qid];r=cm[qid]
        require(g.get("query",text)==text and r["length"]==len(text),"QUERY_TEXT")
        es,err=parse_gold(text,g.get("query_entities"));require(err is None,"QUERY_GOLD_PARSE")
        gold_person=[e for e in es if e["label"]=="person"]
        if not gold_person:continue
        psp=[e for e in r["entities"] if e["label"]=="person"]
        conditions=[(canonicalize("person",text[e["start"]:e["end"]]),role_of(own,text,e)) for e in gold_person]
        detected={canonicalize("person",text[e["start"]:e["end"]]) for e in psp}
        gold_names={n for n,_ in conditions}
        exact=set().union(*(by_name[n] for n,_ in conditions))
        alias={i for i,n in enumerate(nodes) if any(suffix(n["name"],x) for x,_ in conditions)}-exact
        wanted={role for _,role in conditions if role!="역할 미확인"}
        role_candidates={i for i in exact if any(nodes[i]["name"]==n and role!="역할 미확인" and nodes[i]["role"]==role for n,role in conditions)}
        possible={i for i in exact if any(nodes[i]["name"]==n and (role=="역할 미확인" or nodes[i]["role"] in (role,"역할 미확인")) for n,role in conditions)}
        by_cond=[set().union(*(node_pids[i] for i in by_name[n])) for n,_ in conditions]
        ambiguous=any(len(x)>1 for x in by_cond)
        rel=set(g["relevant_doc_ids"])&ready;conf=set(g.get("confusable_doc_ids",[]))&ready
        exact_rel={i for i in exact if nodes[i]["doc"] in rel}
        alias_rel={i for i in alias if nodes[i]["doc"] in rel}
        # Relevance can include multiple people and is NOT target PID annotation.
        rel_pids=set().union(*(node_pids[i] for i in exact_rel))
        rel_pairs=Counter()
        for i,j in combinations(sorted(exact_rel),2):
            if nodes[i]["doc"]==nodes[j]["doc"]:continue
            if not(node_pids[i]&node_pids[j]):continue
            rel_pairs["same_gold_pid_subject_pairs"]+=1
            if evidence(nodes[i],nodes[j],shared)["usable"]:rel_pairs["shared_anchor_pairs"]+=1
            if tuple(sorted((nodes[i]["id"],nodes[j]["id"]))) in links:rel_pairs["batch_link_pairs"]+=1
        def stats(ids):
            ds={nodes[i]["doc"] for i in ids}
            return dict(docs=len(ds),relevant=len(ds&rel),confusable=len(ds&conf))
        linked_rel={i for i in exact_rel if any(nodes[i]["id"] in pair for pair in links)}
        row=dict(qid=qid,ambiguous_name=ambiguous,gold_person_spans=len(gold_person),
            detected_extra_name_values=len(detected-gold_names),detected_missing_name_values=len(gold_names-detected),
            explicit_role=bool(wanted),ready_relevant_docs=len(rel),ready_confusable_docs=len(conf),
            exact=stats(exact),exact_or_suffix=stats(exact|alias),exact_role=stats(role_candidates),
            exact_role_or_unknown=stats(possible),relevant_matching_gold_ids=len(rel_pids),
            relevant_matching_docs_with_graph_link=len({nodes[i]["doc"] for i in linked_rel}),
            relevant_matching_docs_without_graph_link=len({nodes[i]["doc"] for i in exact_rel-linked_rel}),
            relevant_same_pid_subject_pairs=rel_pairs["same_gold_pid_subject_pairs"],
            relevant_shared_anchor_pairs=rel_pairs["shared_anchor_pairs"],
            relevant_batch_link_pairs=rel_pairs["batch_link_pairs"])
        emit("QUERY_IDENTITY_EVIDENCE",**row)
        for c in ([all_counts,amb_counts] if ambiguous else [all_counts]):
            c["queries"]+=1
            c["explicit_role"]+=bool(wanted)
            c["relevant_exact_hit"]+=bool(row["exact"]["relevant"])
            c["relevant_exact_all"]+=bool(rel) and row["exact"]["relevant"]==len(rel)
            c["relevant_suffix_adds_docs"]+=row["exact_or_suffix"]["relevant"]>row["exact"]["relevant"]
            c["role_filter_loses_relevant"]+=row["exact_role_or_unknown"]["relevant"]<row["exact"]["relevant"]
            c["role_filter_reduces_confusable"]+=row["exact_role_or_unknown"]["confusable"]<row["exact"]["confusable"]
            c["relevant_match_has_no_graph_link"]+=bool(exact_rel) and not linked_rel
            c["relevant_match_has_unlinked_subject"]+=bool(exact_rel-linked_rel)
            c["multiple_relevant_matching_gold_ids"]+=len(rel_pids)>1
            c["multiple_relevant_same_pid_pairs_no_shared_anchor"]+=bool(rel_pairs["same_gold_pid_subject_pairs"]) and not rel_pairs["shared_anchor_pairs"]
            c["any_relevant_same_pid_pair_not_linked"]+=rel_pairs["same_gold_pid_subject_pairs"]>rel_pairs["batch_link_pairs"]
    require(all_counts["queries"]==55 and amb_counts["queries"]==12,"Q55_AMBIGUITY_PARITY")
    emit("QUERY_IDENTITY_SUMMARY",all_name_queries=dict(all_counts),ambiguous_name_queries=dict(amb_counts),
         scope="Gold name spans and relevant-doc membership diagnostic; no answerability labels, target PID labels, semantic ranking, ACL or evidence gate measured")
if __name__=="__main__":main()
