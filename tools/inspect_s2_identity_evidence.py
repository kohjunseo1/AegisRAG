"""S2 identity evidence inventory. Local, cached, no model/DB/API calls.
Names are VALUE groups, never assumed to be unique persons. Co-occurrence
is not ownership. Inspect person Gold schema without printing any values.
"""
from collections import Counter,defaultdict
import argparse,hashlib,json,re
from pathlib import Path
from starter.s2_pseudonymizer import canonicalize,V7_POLICY
from starter.v7_model_store import read_manifest
from tools.benchmark_step1 import load_data,require
from tools.benchmark_s2_prepare import queries
from tools.audit_s2_query_gold import parse_gold

ANCHORS={"phone_number","resident_registration_number","email","account_number","card_number"}
ALL_ATTRS=ANCHORS|{"address"}
# Never output arbitrary dictionary keys (which can themselves be PII).
SAFE_FIELDS=set("""id doc_id document_id document_id_list doc_ids document_ids qid query_id
person_id person_ids pid pids entity_id entity_ids identity_id identity_ids
canonical_id canonical_name canonical_person_id canonical_person
gold_person_id gold_id gold_people gold_persons gold_entities gold_mentions
person people persons person_mentions mentions mention mention_id mention_ids
subject subjects name names full_name aliases alias name_variants
role roles type label entity_type text value surface original start end
start_offset end_offset span spans offsets entities attributes attribute
identifiers identifier evidence owned owned_attributes ownership owner owner_id
owner_person_id owner_name person_name owner_mention_id
phone phone_number phone_numbers phones rrn resident_registration_number
resident_registration_numbers email emails address addresses account account_number
account_numbers card card_number card_numbers relation relations relationship
relationships links same_as same_person same_person_id cluster cluster_id
cluster_ids members records documents document data status reason note notes
source source_doc_id source_start source_end metadata version confidence
normalized normalized_value token tokens value_type field values occurrence
occurrences is_ambiguous ambiguous is_homonym homonym group group_id
relevant_doc_ids confusable_doc_ids query_entities query""".split())

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def jsonl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]

def inventory(docs,spanmap):
    names=defaultdict(set);appear=Counter();label_counts=Counter();counts=Counter();doc_shapes=Counter()
    for d in docs:
        did=str(d["id"]);text=d["text"];spans=spanmap[did]
        labels=Counter(e["label"] for e in spans);label_counts.update(labels)
        persons=[e for e in spans if e["label"]=="person"]
        nvals=set()
        for e in persons:
            norm=canonicalize("person",text[e["start"]:e["end"]])
            if norm:names[norm].add(did);appear[norm]+=1;nvals.add(norm)
        counts["documents"]+=1;counts["documents_with_name"]+=bool(persons)
        counts["documents_with_multiple_name_spans"]+=len(persons)>1
        counts["documents_with_multiple_distinct_name_values"]+=len(nvals)>1
        counts["documents_with_name_and_phone_or_rrn"]+=bool(persons and (labels["phone_number"] or labels["resident_registration_number"]))
        counts["documents_with_name_without_phone_or_rrn"]+=bool(persons and not(labels["phone_number"] or labels["resident_registration_number"]))
        counts["documents_with_name_without_any_other_pii"]+=bool(persons and not any(labels[l] for l in ALL_ATTRS))
        counts["documents_with_name_and_any_anchor"]+=bool(persons and any(labels[l] for l in ANCHORS))
        counts["documents_with_no_name"]+=not bool(persons)
        if persons:
            doc_shapes[f"{len(nvals)}_distinct_name_values"]+=1
        # Same-line evidence is only an observable structural proxy.
        for e in spans:
            if e["label"] not in ALL_ATTRS:continue
            a,b=e["start"],e["end"]
            left=text.rfind("\n",0,a)+1
            right=text.find("\n",b)
            if right<0:right=len(text)
            same=[p for p in persons if left<=p["start"]<p["end"]<=right]
            counts["attribute_spans"]+=1
            counts["attribute_spans_same_line_one_name_span"]+=len(same)==1
            counts["attribute_spans_same_line_multiple_name_spans"]+=len(same)>1
            counts["attribute_spans_same_line_no_name_span"]+=not same
    counts["distinct_normalized_name_values"]=len(names)
    counts["name_values_seen_in_multiple_documents"]=sum(len(v)>1 for v in names.values())
    counts["name_values_seen_once"]=sum(v==1 for v in appear.values())
    counts["name_values_in_one_document"]=sum(len(v)==1 for v in names.values())
    return dict(counts),dict(label_counts),dict(sorted(doc_shapes.items()))

def schema(rows):
    paths=defaultdict(Counter)
    id_values=defaultdict(Counter)
    id_fields={"person_id","pid","entity_id","identity_id","canonical_id","canonical_person_id","gold_person_id","same_person_id","cluster_id","owner_person_id","owner_id","mention_id"}
    def walk(v,p="$",depth=0):
        if depth>10:
            paths[p]["depth_limit"]+=1;return
        kind="null" if v is None else type(v).__name__
        paths[p][kind]+=1
        if isinstance(v,dict):
            for k,x in v.items():
                field=k if k in SAFE_FIELDS else "{other_key}"
                path=p+"."+field
                if field in id_fields and isinstance(x,(str,int)) and not isinstance(x,bool):
                    id_values[path][(type(x).__name__,x)]+=1
                walk(x,path,depth+1)
        elif isinstance(v,list):
            for x in v:walk(x,p+"[]",depth+1)
    for r in rows:walk(r)
    return dict(rows=len(rows),paths={k:dict(v) for k,v in sorted(paths.items())},
                possible_id_fields={p:dict(non_null=sum(v.values()),distinct_values=len(v),repeated_values=sum(n>1 for n in v.values())) for p,v in sorted(id_values.items())},
                warning="Schema and possible ID counts only; ID semantics and ownership are not yet verified.")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document-cache",type=Path,required=True)
    a=p.parse_args();root=Path.home()/"AegisRAG/step2_privacy_rag/local_eval"
    dp=root/"holdout_labeled.jsonl";gp=root/"holdout_persons_gold.jsonl"
    qp=root/"holdout_retrieval_queries.jsonl";qg=root/"holdout_retrieval_queries_gold.jsonl"
    docs=load_data("A187",dp);dc=json.loads(a.document_cache.expanduser().read_text())
    m=read_manifest(Path(__file__).resolve().parents[1]/"models/v7_model_release.json")
    require(dc["document_sha256"]==sha(dp) and dc["policy"]==V7_POLICY and dc["model_revision"]==m["revision"],"CACHE_MISMATCH")
    dm={str(r["doc_id"]):r for r in dc["private_document_offsets"]}
    require(len(dm)==len(dc["private_document_offsets"])==len(docs)==187 and set(dm)=={str(d["id"]) for d in docs},"DOC_IDS")
    for d in docs:
        r=dm[str(d["id"])]
        require(r["length"]==len(d["text"]),"DOC_LENGTH")
        require(all(type(e["start"]) is int and type(e["end"]) is int and 0<=e["start"]<e["end"]<=len(d["text"]) for e in r["entities"]),"DOC_OFFSETS")
    ready=[d for d in docs if dm[str(d["id"])]["decision"]=="READY_FOR_LOCAL_PSEUDONYMIZATION"]
    require(len(ready)==184,"READY_BASELINE")
    for scope,ds,spans in (
        ("gold_all",docs,{str(d["id"]):d["entities"] for d in docs}),
        ("gold_ready",ready,{str(d["id"]):d["entities"] for d in ready}),
        ("v7_ready",ready,{str(d["id"]):dm[str(d["id"])]["entities"] for d in ready})):
        counts,labels,shapes=inventory(ds,spans)
        print("IDENTITY_EVIDENCE",json.dumps(dict(scope=scope,counts=counts,label_spans=labels,document_name_value_counts=shapes),ensure_ascii=False),flush=True)
    qs=queries(qp);qgold=jsonl(qg);gm={g["qid"]:g for g in qgold}
    require(len(qs)==len(qgold)==len(gm)==88 and set(gm)=={q["qid"] for q in qs},"QUERY_IDS")
    qc=Counter()
    for q in qs:
        g=gm[q["qid"]];require(g.get("query",q["text"])==q["text"],"QUERY_TEXT")
        es,err=parse_gold(q["text"],g.get("query_entities"));require(err is None,"QUERY_GOLD_PARSE")
        ls=Counter(e["label"] for e in es);n=ls["person"]
        qc["queries"]+=1;qc["queries_with_person"]+=bool(n)
        qc["queries_with_multiple_person_spans"]+=n>1
        qc["queries_with_person_and_phone_or_rrn"]+=bool(n and (ls["phone_number"] or ls["resident_registration_number"]))
        qc["queries_with_person_and_any_other_pii"]+=bool(n and any(ls[l] for l in ALL_ATTRS))
        qc["queries_with_person_only_pii"]+=bool(n and not any(ls[l] for l in ALL_ATTRS))
    print("IDENTITY_QUERY_GOLD",json.dumps(dict(qc),ensure_ascii=False),flush=True)
    if gp.is_file():
        print("PERSON_GOLD_SCHEMA",json.dumps(dict(sha256=sha(gp),**schema(jsonl(gp))),ensure_ascii=False),flush=True)
    else:print("PERSON_GOLD_SCHEMA_MISSING",flush=True)
    print("IDENTITY_EVIDENCE_COMPLETE",flush=True)

if __name__=="__main__":main()
