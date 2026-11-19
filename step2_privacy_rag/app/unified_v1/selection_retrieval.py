"""Read-only DB / isolated-memory retrieval and fail-closed local Mock diagnostics."""
from __future__ import annotations
import argparse
import ast
from collections import Counter,defaultdict
from dataclasses import replace
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace
from .selection import (OUT,PRIVATE,DATA,A,REFERENCE,write_json,write_csv,summary_ms,
                        load_rows,snapshot,ModelSession,subset,fingerprint)
from .contracts import PreparationResult,ContractError
from .postgres import ExistingIndexReader
from .retrieval import ExperimentApproval,QuerySpec,TOKEN,rank_documents
from .embeddings import KoE5Encoder
from .experiments import frozen_rank,local_approve
from .generation import PrivacyRecheck,ContextBuilder,MockTransport,MockGenerator
from .sidecar import create_sidecar,legacy_lookup_key
from dataset_preparation import fixed_key

ENV=Path('/Users/gojunseo/AegisRAG/.env')
ARTIFACT=DATA/'koe5_v6_private/sanitized_eval.json'

def pure_reference(filename,function, *, reference=None):
    tree=ast.parse(((REFERENCE if reference is None else Path(reference))/'starter'/filename).read_text());ns={}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function]
    assert len(nodes)==1
    exec(compile(ast.Module(body=nodes,type_ignores=[]),filename,'exec'),ns)
    return ns[function]

def query_spec(reader,q):
    prep=PreparationResult('LOCAL_PREPARED','LINKED','AUTO_APPROVED',q['text'],reader.key_tag,reader.policy_version,'query',coverage_verified=True)
    receipt=ExperimentApproval(reader.corpus_id,fingerprint(q['text']),reader.key_tag,reader.policy_version,'query')
    return QuerySpec(prep,receipt,tuple(q.get('region_keys',())))

def score_ranking(actual,gold,eligible):
    """Neutral removal is scoring only; retrieval never sees Gold.

    Match frozen scorer: remove neutral from available ranking before scored k.
    Also report online k on the unchanged actual list. Only ten results exist;
    scored top10 can therefore contain fewer than ten documents.
    """
    relevant=set(gold['relevant_doc_ids']);neutral=set(gold.get('neutral_doc_ids',[]));conf=set(gold.get('confusable_doc_ids',[]))
    result=dict(eligible=int(eligible))
    filtered=[d for d in actual[:10] if d not in neutral]
    for k in (1,5,10):
        scored=filtered[:k]
        result['hit'+str(k)]=int(bool(set(scored)&relevant))
        result['online_hit'+str(k)]=int(bool(set(actual[:k])&relevant))
        result['recall'+str(k)]=len(set(scored)&relevant)/len(relevant) if relevant else 0
        result['confusable'+str(k)]=int(bool(set(actual[:k])&conf))
        result['confusable_doc_count'+str(k)]=len(set(actual[:k])&conf)
    result['mrr10']=next((1/(i+1) for i,d in enumerate(filtered) if d in relevant),0)
    return result

def ranking_summary(name,rankings,queries,gold,mode):
    counts=Counter();eligible=0;details=[]
    for q in queries:
        qid=q['qid'];actual=rankings.get(qid,[]);ok=qid in rankings
        m=score_ranking(actual,gold[qid],ok);counts.update(m);eligible+=ok
        details.append(dict(experiment=name,qid=qid,state='RETRIEVED' if ok else 'DEFERRED',
                            actual_top10=json.dumps(actual),scored_top10=json.dumps([d for d in actual if d not in gold[qid].get('neutral_doc_ids',[])]),**m))
    row=dict(experiment=name,mode=mode,full=len(queries),eligible=eligible)
    for key in ('hit1','hit5','hit10','online_hit1','online_hit5','online_hit10','recall5','recall10','mrr10','confusable1','confusable5','confusable10','confusable_doc_count5','confusable_doc_count10'):
        row[key+'_full']=counts[key]/len(queries)
        row[key+'_eligible']=counts[key]/eligible if eligible else None
    return row,details

def chunk_rank(spec,chunks,vectors,query_vector,merge,k=10):
    qt=set(TOKEN.findall(spec.preparation.sanitized_text));qr=set(spec.regions)
    ordered=sorted((dict(doc_id=c['doc_id'],chunk_id=c['chunk_id'],score=sum(a*b for a,b in zip(v,query_vector)),
                         tokens=set(TOKEN.findall(c['masked_text'])),regions=set(c['regions'])) for c,v in zip(chunks,vectors)),key=lambda r:(-r['score'],r['chunk_id']))
    dense=ordered[:max(k*3,50)];region=[r for r in ordered if r['regions']&qr][:k*3] if qr else []
    if not qt:groups=[region,dense] if region else [dense]
    else:
        both=[r for r in ordered if qt<=r['tokens'] and qr<=r['regions']][:k*3] if qr else []
        allhit=[r for r in ordered if qt<=r['tokens']][:k*3]
        anyhit=[r for r in ordered if qt&r['tokens']][:k*3]
        groups=[both,allhit,anyhit,region,dense]
    return [r['doc_id'] for r in merge(groups,k)]

def encoded(encoder,prepared):
    contract=json.dumps(encoder.cfg,sort_keys=True)
    path=PRIVATE/'vectors'/(fingerprint(contract+'\0'+prepared.input_kind+'\0'+prepared.sanitized_text)+'.json')
    if path.exists():
        v=json.loads(path.read_text())
        if len(v)!=1024 or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) or abs(sum(x*x for x in v)-1)>.005:
            raise ContractError('INVALID_CACHED_EMBEDDING')
        return v
    v=encoder.encode_query(prepared) if prepared.input_kind=='query' else encoder.encode_passage(prepared)
    write_json(path,v,True);return v

def require_lock():
    lock=json.loads((OUT/'SELECTION_LOCK.json').read_text())
    if lock['policies']!=['F0']:raise ContractError('SELECTED_CANDIDATE_REQUIRES_EXPLICIT_REPROCESSING')

def retrieval_experiment():
    require_lock()
    data=json.loads(ARTIFACT.read_text());queries=data['queries'];gold={g['qid']:g for g in data['gold_for_evaluation_only']}
    if len(queries)!=88 or len(gold)!=88 or {q['qid'] for q in queries}!=set(gold):raise ContractError('DATASET_QID_MISMATCH')
    reader=ExistingIndexReader(ENV,policy_version='legacy_v6_query');before=reader.metadata()
    if data['hmac_key_tag']!=reader.key_tag:raise ContractError('ARTIFACT_KEY_MISMATCH')
    key=fixed_key(ENV);docs=reader.snapshot_documents()
    side=create_sidecar(docs,index_fingerprint=before['row_fingerprint'],key=key,key_tag=reader.key_tag);reader.attach_sidecar(side,key)
    encoder=KoE5Encoder();rankings={p:{} for p in ('own_v6','team_v3','hybrid_v1','team_groups_chunks600','team_groups_chunks200')}
    write_json(OUT/'RETRIEVAL_LOCK.json',dict(chunk_sizes=[600,200],threshold_tuning=False,
        reason='reference default 600 does not split these documents; fixed 200 line-based ablation provides an actual split, without Gold-based chunking'))
    split=pure_reference('ingest.py','chunk');merge=pure_reference('search.py','_merge')
    with reader._cursor() as cur:
        reader._validate(cur);cur.execute('SELECT doc_id,region_keys FROM rag.aegis_live_docs ORDER BY doc_id');regions=dict(cur.fetchall())
    variants={}
    for size in (600,200):
        chunks=[];vectors=[]
        for number_doc,d in enumerate(docs,1):
            for number,text in enumerate(split(d['masked_text'],size)):
                chunks.append(dict(doc_id=d['doc_id'],chunk_id=d['doc_id']+'#'+str(number),masked_text=text,regions=regions[d['doc_id']]))
                p=PreparationResult('LOCAL_PREPARED','LINKED','AUTO_APPROVED',text,reader.key_tag,reader.policy_version,'document',coverage_verified=True)
                vectors.append(encoded(encoder,p))
            if number_doc%40==0:print(json.dumps(dict(phase='P3',chunk_size=size,documents_encoded=number_doc)),flush=True)
        variants[size]=(chunks,vectors)
    parity=Counter();timing=defaultdict(list)
    for n,q in enumerate(queries,1):
        if q.get('state')!='READY' or not isinstance(q.get('text'),str):continue
        spec=query_spec(reader,q);started=time.perf_counter();v=encoded(encoder,spec.preparation);timing['query_local_embedding_or_cache'].append((time.perf_counter()-started)*1000)
        class SameVector:
            def encode_query(self,p):return v
        for p in ('own_v6','team_v3','hybrid_v1'):
            started=time.perf_counter();rows=reader.retrieve(spec,encoder=SameVector(),profile=p,top_k=10)
            rankings[p][q['qid']]=[r.doc_id for r in rows];timing[p+'_sql_rank'].append((time.perf_counter()-started)*1000)
        original=frozen_rank(reader,spec.preparation,v,spec.regions)
        parity['compared']+=1;parity['mismatches']+=original!=rankings['own_v6'][q['qid']][:5]
        for size,(chunks,vectors) in variants.items():
            started=time.perf_counter();rankings['team_groups_chunks'+str(size)][q['qid']]=chunk_rank(spec,chunks,vectors,v,merge);timing['chunk_rank_'+str(size)].append((time.perf_counter()-started)*1000)
        if n%20==0:print(json.dumps(dict(phase='P3',queries=n)),flush=True)
    if parity['mismatches']:raise ContractError('FROZEN_PARITY_FAILED')
    summaries=[];details=[]
    for name,rs in rankings.items():
        summary,detail=ranking_summary(name,rs,queries,gold,'ISOLATED_MEMORY' if 'chunks' in name else 'REAL_DB')
        summaries.append(summary);details+=detail
    base={d['qid']:d for d in details if d['experiment']=='own_v6'}
    for row in details:
        row['hit5_delta_vs_own']=row['hit5']-base[row['qid']]['hit5']
        row['mrr10_delta_vs_own']=row['mrr10']-base[row['qid']]['mrr10']
    after=reader.metadata();assert before['row_fingerprint']==after['row_fingerprint']
    write_csv('retrieval_same_input.csv',summaries);write_csv('retrieval_qid_top10.csv',details)
    from metrics_retrieval import summarize_score
    legacy={p:summarize_score(queries,data['gold_for_evaluation_only'],set(reader.ids),{q:rs[:5] for q,rs in ranks.items()}) for p,ranks in rankings.items()}
    write_json(OUT/'legacy_top5_metrics.json',legacy)
    write_json(OUT/'retrieval_conditions.json',dict(before=before,after=after,top5_parity=dict(parity),documents=len(docs),chunks={str(size):len(c) for size,(c,v) in variants.items()},
        hmac_key_tag=reader.key_tag,
        chunker='original reference ingest.chunk(max_chars=600 or 200), line boundaries, no hard token split',
        chunk_regions='inherited fixed source-document metadata; not original region.py namespace',
        chunk_ranking='original search._merge with original group limits; isolated memory KoE5, not original rag.chunks/API embedding reproduction',
        neutral='frozen scorer: remove neutral from available top10 then scored k; online metrics use unchanged actual k. Legacy top5-only scoring also saved separately.',
        latency={n:summary_ms(v) for n,v in timing.items()},
        R4='BLOCKED: unapproved external embedding transfer',isolated_sql='BLOCKED: no verified disposable database; no DDL/DML issued'))
    return summaries

class CachedRecheck:
    def __init__(self,session):self.session=session
    def detect(self,text,*,input_kind):
        ev,_,_=snapshot(self.session,text)
        return replace(ev,input_kind=input_kind)

def e2e_experiment(python):
    require_lock()
    # Reprocess documents and queries with actual frozen prepare, no evidence_v2.
    from document_preparation import prepare as frozen_prepare
    from .replay import SnapshotDetector
    from .identity import extract_owned_fields
    from .pipeline import query_from_mentions
    from .contracts import Candidate,ProtectionPlan
    from region_keys import keys_from_query
    reader=ExistingIndexReader(ENV,policy_version='frozen_v6_selection');before=reader.metadata();key=fixed_key(ENV)
    docs=reader.snapshot_documents();dbtext={r['doc_id']:r['masked_text'] for r in docs}
    side=create_sidecar(docs,index_fingerprint=before['row_fingerprint'],key=key,key_tag=reader.key_tag);reader.attach_sidecar(side,key)
    encoder=KoE5Encoder();details=[];private=[];counts=Counter();r3=Counter();reprocessed=[]
    def prep_actual(text,ev,doc_id,kind):
        with SnapshotDetector(text,subset(ev,'own2')) as detector:out=frozen_prepare(('Q_' if kind=='query' else 'DOC_')+doc_id,text,detector,key)
        return out
    with ModelSession(python) as session:
        # R3 current frozen control only; all newly prepared texts stay in memory.
        for r in load_rows(A):
            ev,_,_=snapshot(session,r['text']);out=prep_actual(r['text'],ev,r['id'],'document');r3[out.state]+=1
            if out.state=='LOCAL_PREPARED':
                r3['compared_to_existing']+=1;r3['same_masked_text']+=out.sanitized_text==dbtext.get(r['id'])
                p=PreparationResult('LOCAL_PREPARED','LINKED','AUTO_APPROVED',out.sanitized_text,reader.key_tag,reader.policy_version,'document',coverage_verified=True)
                reprocessed.append((r['id'],p,tuple(keys_from_query(r['text'],key)),encoded(encoder,p)))
        memdocs=[SimpleNamespace(doc_id=did,preparation=p,regions=reg,entity_ids=(),name_lookup_keys=tuple(legacy_lookup_key(key,t) for t in TOKEN.findall(p.sanitized_text) if t.startswith('<PERSON_'))) for did,p,reg,vec in reprocessed]
        guard=PrivacyRecheck(CachedRecheck(session))
        builder=ContextBuilder(guard,key_tag=reader.key_tag,policy_version=reader.policy_version,index_version=reader.index_version,corpus_id=reader.corpus_id,local_read_only=True)
        cases=load_rows(DATA/'holdout_retrieval_queries.jsonl');gold={g['qid']:g for g in json.loads(ARTIFACT.read_text())['gold_for_evaluation_only']}
        if len(cases)!=88 or len({q['qid'] for q in cases})!=88 or {q['qid'] for q in cases}!=set(gold):raise ContractError('DATASET_QID_MISMATCH')
        r3rank={};mockrank={}
        for number,r in enumerate(cases,1):
            qid,raw=r['qid'],r['query'];stage='gate';started=time.perf_counter();outcome='BLOCKED';error='';actual=[];guard.last_diagnostics=()
            try:
                ev,_,_=snapshot(session,raw,profile='own2');original=prep_actual(raw,ev,qid,'query')
                if original.state!='LOCAL_PREPARED':raise ContractError('FROZEN_'+original.state)
                stage='query_prep'
                prep=PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED',original.sanitized_text,reader.key_tag,reader.policy_version,'query',coverage_verified=True)
                # Obtain identity metadata from exact frozen validated entities only.
                from .selection import frozen_outcome
                frozen=frozen_outcome(raw,ev)
                cs=tuple(Candidate('ax','frozen_v6',l,a,b,fingerprint(raw)) for l,a,b in frozen['hmac'])
                plan=ProtectionPlan(cs,cs,frozen['masks'],(),(),(),'own2','frozen_v6_selection',fingerprint(raw),'query')
                identity=extract_owned_fields(raw,plan,doc_id=qid,hmac_key=key,key_tag=reader.key_tag)
                # R3 is a LOCAL retrieval experiment, independent of outbound eligibility.
                localprep=replace(prep,approval_state='AUTO_APPROVED')
                localreceipt=ExperimentApproval(reader.corpus_id,fingerprint(localprep.sanitized_text),reader.key_tag,reader.policy_version,'query')
                localspec=query_from_mentions(localprep,localreceipt,identity.mentions,regions=tuple(keys_from_query(raw,key)))
                v=encoded(encoder,localprep)
                scores={did:sum(a*b for a,b in zip(vec,v)) for did,p,reg,vec in reprocessed}
                r3rank[qid]=[x.doc_id for x in rank_documents(localspec,memdocs,scores,profile='own_v6',top_k=10,index_version='isolated_frozen_v6_177',key_tag=reader.key_tag,policy_version=reader.policy_version,dense_pool_limit=len(memdocs))]
                stage='local_query_recheck';approved,receipt=local_approve(prep,guard,reader.corpus_id)
                spec=query_from_mentions(approved,receipt,identity.mentions,regions=tuple(keys_from_query(raw,key)))
                stage='retrieval';v=encoded(encoder,approved)
                class SameVector:
                    def encode_query(self,p):return v
                rows=reader.retrieve(spec,encoder=SameVector(),profile='own_v6',top_k=5);actual=[x.doc_id for x in rows];mockrank[qid]=actual
                if not rows:raise ContractError('NO_EVIDENCE')
                stage='context_recheck';context=builder.build(spec,rows)
                # ContextBuilder also enforces bounded outbound and checks clipping.
                stage='mock_generation'
                transport=MockTransport(dict(decision='ANSWER',answer='로컬 검색 문서를 확인했습니다.',citations=[rows[0].doc_id]))
                MockGenerator(builder,transport).generate(spec,rows)
                stage='complete';outcome='MOCK_PASS'
            except ContractError as exc:
                error=str(exc)
                if stage in {'context_recheck','mock_generation'}:stage=getattr(builder,'last_stage',stage)
            counts[stage]+=1
            diag=tuple(guard.last_diagnostics)
            if stage=='gate':
                diag=tuple(dict(source=c.source_id,label=c.label,start=c.start,end=c.end,rule_codes=c.evidence_codes)
                           for candidates in ev.raw_candidates_by_source.values() for c in candidates)
            classification=('TOKEN_OR_VERSION_MISMATCH' if any(w in error for w in ('TOKEN','VERSION','KEY_MISMATCH')) else
                            'POLICY_MISMATCH' if diag and all(d.get('label','').startswith('extra_sensitive:') for d in diag) else
                            'UNKNOWN' if error else 'NOT_APPLICABLE')
            details.append(dict(qid=qid,detector='F0',retriever='own_v6',outcome=outcome,first_failure_stage=stage,error_code=error,
                classification=classification,manual_review='PENDING' if error else 'NOT_REQUIRED',
                model_labels=json.dumps(sorted({d.get('source','')+':'+d.get('label','') for d in diag})),
                candidate_count=len(diag),latency_ms=(time.perf_counter()-started)*1000,actual_top5=json.dumps(actual)))
            private.append(dict(qid=qid,stage=stage,classification=classification,diagnostics=diag,
                                offset_unit='unicode_codepoint_in_token_blanked_sanitized_input' if stage!='gate' else 'unicode_codepoint_original',
                                subject=getattr(builder,'last_subject','query') if stage in {'context_recheck','outbound','output_recheck'} else 'query'))
            if number%10==0:
                write_csv('e2e_gate_diagnostics.csv',details);write_json(PRIVATE/'e2e_diagnostics.json',private,True)
                print(json.dumps(dict(phase='P4',queries=number,stages=dict(counts))),flush=True)
        write_csv('e2e_gate_diagnostics.csv',details);write_json(PRIVATE/'e2e_diagnostics.json',private,True)
        r3summary,r3detail=ranking_summary('R3_F0_reprocessed_READY_only',r3rank,cases,gold,'ISOLATED_MEMORY')
        mocksummary,mockdetail=ranking_summary('F0_own_v6_mock_eligible',mockrank,cases,gold,'REAL_DB_MOCK_HCX')
        mocksummary['returned_limit']=5
        mocksummary['mrr_available_full']=mocksummary['mrr10_full'];mocksummary['mrr_available_eligible']=mocksummary['mrr10_eligible']
        for k in list(mocksummary):
            if '10_' in k:mocksummary[k]=None
        for row in mockdetail:
            row['returned_limit']=5
            for k in ('hit10','online_hit10','recall10','mrr10','confusable10','confusable_doc_count10'):row[k]=None
        write_csv('retrieval_reprocessed.csv',[r3summary,mocksummary]);write_csv('retrieval_reprocessed_qids.csv',r3detail+mockdetail)
    after=reader.metadata();assert before['row_fingerprint']==after['row_fingerprint']
    result=dict(mode='REAL_MODEL_REAL_KOE5_REAL_DB_MOCK_HCX',queries=len(details),stages=dict(counts),document_reprocess=dict(r3),
                paid_calls=0,external_private_transfers=0,row_fingerprint_unchanged=True,
                factuality='NOT_MEASURED_MOCK_ONLY',false_positive_manual_review='PENDING; no unproved whitelist added',
                leak_review='NOT_MEASURED on real documents; guard remains fail-closed',
                identity='unified token-derived lookup; no legacy entity ownership invented',
                latency=summary_ms([r['latency_ms'] for r in details]))
    write_json(OUT/'e2e_summary.json',result);return result

def audit_indexability(python):
    """Independent local guard on every current V6 READY document, no DB writes.

    The public diagnostic key is sufficient: valid tokens are blanked preserving
    offsets before NER, so private production keys are unnecessary here.
    """
    from document_preparation import prepare as original_prepare
    from .replay import SnapshotDetector
    from .experiments import DIAGNOSTIC_KEY
    counts=Counter();details=[];private=[]
    with ModelSession(python) as session:
        guard=PrivacyRecheck(CachedRecheck(session))
        for n,r in enumerate(load_rows(A),1):
            ev,_,_=snapshot(session,r['text'])
            with SnapshotDetector(r['text'],subset(ev,'own2')) as detector:
                out=original_prepare(r['id'],r['text'],detector,DIAGNOSTIC_KEY)
            if out.state!='LOCAL_PREPARED':counts['FROZEN_'+out.state]+=1;continue
            counts['FROZEN_READY']+=1;error=''
            try:guard.check(out.sanitized_text);counts['INDEPENDENT_GUARD_PASS']+=1
            except ContractError as exc:error=str(exc);counts['INDEPENDENT_GUARD_BLOCKED']+=1
            details.append(dict(doc_id=r['id'],guard='BLOCKED' if error else 'PASS',error_code=error,
                classification='UNKNOWN' if error else 'NOT_APPLICABLE',manual_review='PENDING' if error else 'NOT_REQUIRED',
                model_labels=json.dumps(sorted({d.get('source','')+':'+d.get('label','') for d in guard.last_diagnostics}))))
            if error:private.append(dict(doc_id=r['id'],diagnostics=guard.last_diagnostics,offset_unit='unicode_codepoint_token_blanked_sanitized'))
            if n%30==0:print(json.dumps(dict(phase='P4_INDEXABILITY',documents=n,counts=dict(counts))),flush=True)
    write_csv('indexability_guard.csv',details);write_json(PRIVATE/'indexability_diagnostics.json',private,True)
    write_json(OUT/'indexability_summary.json',dict(counts=dict(counts),sql_writes=0,
        scope='local independent-guard eligibility, not remote corpus approval or actual insertion; no residual false-positive assumption'))
    return counts

def synthetic_selected(python):
    """P5 selected F0 x own_v6, actual local models/KoE5, new memory index."""
    require_lock()
    from document_preparation import prepare as original_prepare
    from .replay import SnapshotDetector
    from .experiments import DIAGNOSTIC_KEY
    from dataset_preparation import key_tag
    from .retrieval import InMemoryIndex,IndexedDocument
    tag=key_tag(DIAGNOSTIC_KEY);policy='frozen_v6_selection';counts=Counter()
    docs=[('synthetic_education','고객명: 금재원\n전화: 010-0000-0000\n교육 신청은 승인 후 완료됩니다.'),
          ('synthetic_travel','출장비 신청서는 재무 부서에 제출합니다.'),
          ('synthetic_security','보안 교육은 금요일에 진행됩니다.')]
    encoder=KoE5Encoder();vectors={}
    with ModelSession(python) as session:
        guard=PrivacyRecheck(CachedRecheck(session))
        def prepare_text(did,text,kind):
            ev=session.infer(text,profile='own2',input_kind=kind)
            with SnapshotDetector(text,ev) as detector:out=original_prepare(('Q_' if kind=='query' else 'DOC_')+did,text,detector,DIAGNOSTIC_KEY)
            if out.state!='LOCAL_PREPARED':raise ContractError('SYNTHETIC_FROZEN_NOT_READY')
            return PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED',out.sanitized_text,tag,policy,kind,coverage_verified=True)
        ix=InMemoryIndex(key_tag=tag,policy_version=policy,corpus_id='selection_synthetic',index_version='selection_synthetic_v1')
        for did,text in docs:
            p=prepare_text(did,text,'document');approved,receipt=local_approve(p,guard,ix.corpus_id)
            ix.add(IndexedDocument(did,approved,receipt));vectors[did]=encoder.encode_passage(approved);counts['indexed']+=1
        q=prepare_text('synthetic_query','교육 신청 절차를 알려주세요.','query');p,a=local_approve(q,guard,ix.corpus_id);spec=QuerySpec(p,a)
        vector=encoder.encode_query(p)
        rows=ix.retrieve(spec,profile='own_v6',dense_scorer=lambda q,ds:{d.doc_id:sum(x*y for x,y in zip(vectors[d.doc_id],vector)) for d in ds})
        builder=ContextBuilder(guard,key_tag=tag,policy_version=policy,index_version=ix.index_version,corpus_id=ix.corpus_id)
        transport=MockTransport(dict(decision='ANSWER',answer='검색된 합성 문서를 확인했습니다.',citations=[rows[0].doc_id]))
        result=MockGenerator(builder,transport).generate(spec,rows)
        counts['mock_calls']=transport.calls;counts['mock_pass']+=1
    output=dict(status='PASS',mode='REAL_MODEL_FROZEN_V6_REAL_KOE5_ISOLATED_MEMORY_MOCK_HCX',counts=dict(counts),
                selected_detector='F0',selected_retriever='own_v6',paid_calls=0,sql_writes=0,factuality='NOT_MEASURED_MOCK_ONLY')
    write_json(OUT/'synthetic_selected.json',output);return output

def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['P3','P4','P5']);parser.add_argument('--runtime-python',required=True)
    parser.add_argument('--indexability-only',action='store_true')
    args=parser.parse_args()
    if args.indexability_only:audit_indexability(args.runtime_python);return
    if args.phase=='P3':retrieval_experiment()
    elif args.phase=='P4':e2e_experiment(args.runtime_python)
    else:synthetic_selected(args.runtime_python)
    print(args.phase+' COMPLETE')

if __name__=='__main__':main()
