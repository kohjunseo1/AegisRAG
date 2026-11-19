"""Reproducible local-only experiments. Text/spans stay in memory, summaries only."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import ast
import collections
import contextlib
import io
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import time
from .contracts import (ContractError,DetectionEvidence,PROFILES,LABELS,EXTRA_LABELS,config_hash,fingerprint)
from .adapters import PersistentPredictor,LocalHFPredictor,koelectra_label
from .runtime import KLUE_HASHES,KOELECTRA_HASHES,build_engine,benchmark_predictor,profile_status
from .replay import SnapshotGateAdapter,SnapshotDetector
from .local_validation import FIXTURES

PROFILES_TO_EVAL=('own2','quality3','quality4','team2','drop_ax','drop_veil','drop_klue','drop_koelectra')

class ModelSession:
    def __init__(self,python, *, timeout=120):
        self.python=str(Path(python).absolute());self.timeout=timeout;self.stack=ExitStack()
        self.workers={};self._engines={};self.sampled_child_rss_peak_mb=0
    def __enter__(self):
        temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='aegis-final-models-')))
        root=Path.home()/'private-models/aegisrag'
        manifests={'own2':dict(source_id='own2',source_revision='frozen-v6')}
        for s,folder,hashes in [('klue','roberta_small_v1',KLUE_HASHES),('koelectra','koelectra_ft5',KOELECTRA_HASHES)]:
            manifests[s]=dict(source_id=s,source_revision=config_hash(hashes),model_dir=str(root/folder),
                              trusted_root=str(root),hashes=hashes,max_length=512,stride=64)
        worker=str(Path(__file__).with_name('worker.py').resolve())
        for s,m in manifests.items():
            path=temp/(s+'.json');path.write_text(json.dumps(m));path.chmod(0o600)
            self.workers[s]=self.stack.enter_context(PersistentPredictor([self.python,worker,str(path)],config_hash(m),timeout=self.timeout))
        return self
    def __exit__(self,*args):return self.stack.__exit__(*args)
    def engine(self,profile):
        if profile not in self._engines:
            self._engines[profile]=build_engine(profile,own_worker=self.workers['own2'],klue_worker=self.workers['klue'],koelectra_worker=self.workers['koelectra'])
        return self._engines[profile]
    def infer(self,text, *, input_kind='document',profile='quality4'):
        evidence=self.engine(profile).detect(text,input_kind=input_kind)
        pids=[str(w._process.pid) for w in self.workers.values() if w._process is not None]
        if pids:
            try:
                # Avoid subprocess/fork after a KoE5 tokenizer has initialized.
                import sys,ctypes,struct
                rss=[]
                if sys.platform=='darwin':
                    lib=ctypes.CDLL('/usr/lib/libproc.dylib')
                    for pid in pids:
                        buffer=ctypes.create_string_buffer(96)
                        if lib.proc_pidinfo(int(pid),4,0,buffer,96)>=16:
                            rss.append(struct.unpack_from('QQ',buffer.raw)[1]/1024**2)
                elif sys.platform.startswith('linux'):
                    for pid in pids:
                        rss.append(int(Path('/proc',pid,'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1024**2)
                if rss:self.sampled_child_rss_peak_mb=max(self.sampled_child_rss_peak_mb,sum(rss))
            except Exception:pass
        return evidence


def subset(evidence,profile):
    sources=PROFILES[profile]
    return DetectionEvidence(evidence.input_kind,evidence.request_id,{s:evidence.raw_candidates_by_source[s] for s in sources},
                             {s:evidence.model_status_by_source[s] for s in sources},(),
                             config_hash({'parent':evidence.config_hash,'profile':profile}),evidence.original_input_fingerprint,profile)


def check_models(python):
    result=dict(mode='REAL_LOCAL_SYNTHETIC',models={},api_calls=0)
    with ModelSession(python) as session:
        for source,worker in session.workers.items():
            try:
                rows=[worker(t) for t in FIXTURES]
                result['models'][source]=dict(status='LOCAL_MODEL_PASS',fixtures=len(rows),benchmark=benchmark_predictor(worker,FIXTURES[0]),
                                               candidate_counts=[{s:len(v) for s,v in r.items()} if isinstance(r,dict) else len(r) for r in rows])
            except Exception as exc:result['models'][source]=dict(status='BLOCKED',error_type=type(exc).__name__)
        evidence=session.infer(FIXTURES[0])
        result['four_model_status']=dict(evidence.model_status_by_source)
        valid={s:'LOCAL_MODEL_PASS' for s,v in evidence.model_status_by_source.items() if v=='OK'}
        result['profiles']={p:profile_status(p,valid) for p in PROFILES}
        result['sampled_child_rss_peak_mb']=session.sampled_child_rss_peak_mb
    return result


def koelectra_parity(reference_dir):
    """Reference raw decode on exactly the same logits/tags, policy changes separate."""
    import torch
    source=Path(reference_dir)/'starter/hf_detector.py'
    tree=ast.parse(source.read_text())
    ns={'PREFIX':re.compile(r'^(?:[BIESLU])-')}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='decode'],type_ignores=[]),str(source),'exec'),ns)
    root=Path.home()/'private-models/aegisrag'
    predictor=LocalHFPredictor(str(root/'koelectra_ft5'),str(root),KOELECTRA_HASHES,label_policy='koelectra59')
    matches=[];types=collections.Counter()
    for text in FIXTURES:
        enc=predictor.tok(text,return_offsets_mapping=True,return_tensors='pt',truncation=True,max_length=512)
        offsets=enc.pop('offset_mapping')[0].tolist()
        if getattr(predictor.model.config,'type_vocab_size',2)<=1:enc.pop('token_type_ids',None)
        with torch.inference_mode():out=predictor.model(**enc)
        logits=out.logits if hasattr(out,'logits') else out[0]
        tags=[predictor.model.config.id2label[i] for i in logits[0].argmax(-1).tolist()]
        original=ns['decode'](text,offsets,tags)
        expected=[]
        for row in original:
            types[row['type']]+=1
            lab=koelectra_label('B-'+row['type'])
            if lab is not None:expected.append((lab,row['start'],row['end']))
        actual=[(r['label'],r['start'],r['end']) for r in predictor(text)]
        matches.append(expected==actual)
    coverage={}
    for maximum in [256,512]:
        text='합성 '*700+'끝'
        enc=predictor.tok(text,return_offsets_mapping=True,return_overflowing_tokens=True,truncation=True,max_length=maximum,stride=64)
        coverage[str(maximum)]=dict(windows=len(enc['offset_mapping']),tail_covered=max(b for w in enc['offset_mapping'] for a,b in w)==len(text))
    return dict(raw_decoder_parity='PASS' if all(matches) else 'PARITY_BLOCKED',fixtures=len(matches),
                type_counts=dict(types),overflow=coverage,
                postprocessing_parity='INTENTIONAL_POLICY_DIFFERENCE: no team merge_adjacent/trim; ALIEN_ID extra-sensitive, not RRN',
                model_labels=len(predictor.model.config.id2label),fast_tokenizer=predictor.tok.is_fast)

DIAGNOSTIC_KEY=b'aegis-final-synthetic-parity-key-00000'

def legacy_diagnostic(predictors,text,input_kind):
    """Actual frozen V6 inference, not snapshot replay; diagnostic path only."""
    from .replay import PIIDetector
    from document_preparation import prepare as original_prepare
    class Capture(PIIDetector):
        def detect(self,value):
            self.last_result=super().detect(value)
            return self.last_result
    with Capture(predictors['ax'].predictor,predictors['veil'].predictor,
                 token_guard=predictors['ax'].token_guard,parallel=False) as detector:
        outcome=original_prepare('Q_DIAGNOSTIC' if input_kind=='query' else 'DOC_DIAGNOSTIC',text,detector,DIAGNOSTIC_KEY)
        return dict(state=outcome.state,sanitized_fingerprint=fingerprint(outcome.sanitized_text) if outcome.sanitized_text is not None else None,
                    masks=detector.last_result.get('mask_intervals',[]))

def load_rows(path):
    p=Path(path)
    if not p.is_file():raise ContractError('DATASET_MISSING')
    try:rows=[json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    except Exception:raise ContractError('DATASET_SCHEMA_INVALID') from None
    if not rows or any(not isinstance(r,dict) for r in rows):raise ContractError('DATASET_SCHEMA_INVALID')
    return rows


def prf(predicted,gold):
    tp=len(predicted&gold);fp=len(predicted-gold);fn=len(gold-predicted)
    return tp,fp,fn

def finish_counts(counter):
    tp,fp,fn=counter['tp'],counter['fp'],counter['fn']
    return dict(tp=tp,fp=fp,fn=fn,precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)


def evaluate_step1(python,documents, *, limit=None,legacy_parity=True):
    from .protection import covers,prepare
    from dataset_preparation import key_tag
    from local_pseudonymizer import pseudonymize_local
    rows=load_rows(documents)
    if limit is not None:rows=rows[:limit]
    result=dict(dataset_role='seen_dev',documents=len(rows),gold_entities=0,profiles={},source_hashes=dict(klue=KLUE_HASHES,koelectra=KOELECTRA_HASHES),api_calls=0)
    totals={p:collections.Counter() for p in PROFILES_TO_EVAL}
    by_label={p:collections.defaultdict(collections.Counter) for p in PROFILES_TO_EVAL}
    times=collections.defaultdict(list);parity=collections.Counter();latency=[]
    with ModelSession(python) as session:
        for record in rows:
            text=record.get('text');gold=record.get('entities')
            if not isinstance(text,str) or not isinstance(gold,list):raise ContractError('DATASET_SCHEMA_INVALID')
            expected={(e['label'],e['start'],e['end']) for e in gold}
            if any(l not in LABELS or type(a) is not int or type(b) is not int or not 0<=a<b<=len(text) for l,a,b in expected):
                raise ContractError('INVALID_GOLD_SPAN')
            result['gold_entities']+=len(expected)
            t=time.perf_counter();evidence=session.infer(text);latency.append((time.perf_counter()-t)*1000)
            for source,w in session.workers.items():times[source].append(w.last_metrics.get('inference_ms',0))
            baseline=SnapshotGateAdapter(policy='evidence_v1').check(text,subset(evidence,'own2'))
            if legacy_parity:
                session.workers['own2'](text,diagnostic_legacy=True)
                diag=session.workers['own2'].last_diagnostics
                state=('LOCAL_PREPARED' if baseline.get('decision')=='READY_FOR_LOCAL_PSEUDONYMIZATION' else
                       'BLOCKED' if baseline.get('decision')=='BLOCKED' else 'REVIEW')
                parity['state_mismatches']+=state!=diag['state']
                if state=='LOCAL_PREPARED' and diag['state']=='LOCAL_PREPARED':
                    local=pseudonymize_local(text,baseline,hmac_key=DIAGNOSTIC_KEY).sanitized_text
                    parity['hmac_compared']+=1;parity['hmac_mismatches']+=fingerprint(local)!=diag['sanitized_fingerprint']
                parity['documents']+=1
            else:diag=None
            baseline_masks=[(s['start'],s['end']) for s in baseline.get('mask_intervals',[])+baseline.get('proposed_protection',[])]
            for profile in PROFILES_TO_EVAL:
                ev=subset(evidence,profile);plan=SnapshotGateAdapter(policy='evidence_v2').plan(text,ev)
                c=totals[profile]
                raw={(r.label,r.start,r.end) for rs in ev.raw_candidates_by_source.values() for r in rs if r.label in LABELS}
                typed={(r.label,r.start,r.end) for r in plan.strict_entities}
                for prefix,pred in [('raw',raw),('pipeline',typed)]:
                    tp,fp,fn=prf(pred,expected)
                    c[prefix+'_tp']+=tp;c[prefix+'_fp']+=fp;c[prefix+'_fn']+=fn
                protected={i for a,b in plan.mask_intervals for i in range(a,b)}
                gold_chars={i for l,a,b in expected for i in range(a,b)}
                c['gold_chars']+=len(gold_chars);c['protected_gold_chars']+=len(protected&gold_chars)
                c['completely_missed']+=sum(not protected.intersection(range(a,b)) for l,a,b in expected)
                c['status_'+plan.protection_status]+=1
                c['READY' if not plan.review_reason_codes else 'BLOCKED' if plan.protection_status=='BLOCKED' else 'REVIEW']+=1
                c['hmac_entities']+=len(plan.entities_for_hmac)
                unique={(r.label,r.start,r.end) for r in plan.strict_entities+plan.entities_for_hmac+plan.unresolved_mentions}
                c['candidate_denominator']+=len(unique)
                c['mask_only_documents']+=bool(plan.pending_mask_intervals)
                c['extra_sensitive_candidates']+=sum(r.label in EXTRA_LABELS for rs in ev.raw_candidates_by_source.values() for r in rs)
                c['newly_protected_gold_chars_vs_own2']+=sum(i in protected and not any(a<=i<b for a,b in baseline_masks) for i in gold_chars)
                if {'ax','veil'}<=set(PROFILES[profile]):
                    if any(not covers(m,plan.mask_intervals) for m in baseline_masks):raise ContractError('MASK_MONOTONICITY_FAILED')
                    if diag and any(not covers((m['start'],m['end']),plan.mask_intervals) for m in diag['masks']):
                        parity['mask_regressions']+=1
                for label in LABELS:
                    chars={i for l,a,b in expected if l==label for i in range(a,b)}
                    by_label[profile][label]['gold_chars']+=len(chars)
                    by_label[profile][label]['protected']+=len(protected&chars)
        result['timing']={s:dict(n=len(v),warm_or_corpus_p50_ms=statistics.median(v),p95_ms=sorted(v)[math.ceil(.95*len(v))-1]) for s,v in times.items()}
        result['timing']['quality4_request']=dict(n=len(latency),corpus_p50_ms=statistics.median(latency),p95_ms=sorted(latency)[math.ceil(.95*len(latency))-1])
        result['worker_resources']={s:w.last_metrics for s,w in session.workers.items()}
        result['sampled_child_rss_peak_mb']=session.sampled_child_rss_peak_mb
    for p,c in totals.items():
        result['profiles'][p]=dict(raw=finish_counts(collections.Counter({k:c['raw_'+k] for k in ['tp','fp','fn']})),
                                  pipeline=finish_counts(collections.Counter({k:c['pipeline_'+k] for k in ['tp','fp','fn']})),
                                  character_protection_rate=c['protected_gold_chars']/c['gold_chars'] if c['gold_chars'] else None,
                                  completely_missed=c['completely_missed'],gate={s:c[s] for s in ['READY','REVIEW','BLOCKED']},
                                  protection_states={s:c['status_'+s] for s in ['CONFIRMED_HMAC','NO_PII_CANDIDATES','LOCAL_MASK_ONLY_PENDING_APPROVAL']},
                                  hmac_confirmation_rate=c['hmac_entities']/c['candidate_denominator'] if c['candidate_denominator'] else None,
                                  mask_only_documents=c['mask_only_documents'],extra_sensitive_candidates=c['extra_sensitive_candidates'],
                                  newly_protected_gold_chars_vs_own2=c['newly_protected_gold_chars_vs_own2'],
                                  labels={l:dict(v,coverage=v['protected']/v['gold_chars'] if v['gold_chars'] else None) for l,v in by_label[p].items()})
    result['legacy_parity']={k:parity[k] for k in ['documents','state_mismatches','hmac_compared','hmac_mismatches','mask_regressions']}
    result['legacy_parity_status']='PASS' if legacy_parity and not any(parity[k] for k in ['state_mismatches','hmac_mismatches','mask_regressions']) else 'NOT_RUN' if not legacy_parity else 'FAIL'
    return result


def frozen_rank(reader,prepared,vector,regions):
    from core import order_results,PII
    vec='['+','.join(format(float(v),'.9g') for v in vector)+']'
    with reader._cursor() as cur:
        reader._validate(cur)
        cur.execute('SELECT doc_id,masked_text,tokens,region_keys FROM rag.aegis_live_docs WHERE key_tag=%s ORDER BY embedding <=> %s::vector,doc_id',(reader.key_tag,vec))
        rows=[dict(doc_id=d,text=t,tokens=tk,region_keys=r,dense_rank=i+1) for i,(d,t,tk,r) in enumerate(cur.fetchall())]
    return [r['doc_id'] for r in order_results(rows,set(PII.findall(prepared.sanitized_text)),set(regions),use_topic_boost=False)[:5]]


def evaluate_retrieval(env_file,artifact):
    from .postgres import ExistingIndexReader
    from .embeddings import KoE5Encoder
    from .retrieval import ExperimentApproval,QuerySpec,TOKEN,evaluate_rankings
    from .contracts import PreparationResult
    from .sidecar import create_sidecar,save_sidecar,coverage
    from dataset_preparation import fixed_key
    try:data=json.loads(Path(artifact).read_text())
    except FileNotFoundError:raise ContractError('DATASET_MISSING') from None
    if data.get('format')!='aegisrag-local-embedding-eval-v1':raise ContractError('LEGACY_ARTIFACT_SCHEMA_INVALID')
    queries=data['queries'];gold=data['gold_for_evaluation_only']
    if len(queries)!=88 or len(gold)!=88:raise ContractError('EXPECTED_88_QUERIES')
    reader=ExistingIndexReader(env_file,policy_version='legacy_v6_query')
    before=reader.metadata()
    if data['hmac_key_tag']!=reader.key_tag:raise ContractError('LEGACY_ARTIFACT_KEY_MISMATCH')
    key=fixed_key(Path(env_file))
    side=create_sidecar(reader.snapshot_documents(),index_fingerprint=before['row_fingerprint'],key=key,key_tag=reader.key_tag)
    save_sidecar(Path.home()/'.cache/aegisrag/unified_v1/private/identity_sidecar.json',side)
    reader.attach_sidecar(side,key)
    encoder=KoE5Encoder()
    rankings={p:{} for p in ['own_v6','team_v3','hybrid_v1']};states={};parity=collections.Counter();times=[]
    for q in queries:
        qid=q['qid'];text=q.get('text')
        if not isinstance(text,str) or q.get('state')!='READY':
            states[qid]='REVIEW';continue
        states[qid]='READY'
        prep=PreparationResult('LOCAL_PREPARED','LINKED','AUTO_APPROVED',text,reader.key_tag,reader.policy_version,'query',coverage_verified=True)
        receipt=ExperimentApproval(reader.corpus_id,fingerprint(text),reader.key_tag,reader.policy_version,'query')
        spec=QuerySpec(prep,receipt,tuple(q.get('region_keys',())))
        t=time.perf_counter();vector=encoder.encode_query(prep)
        class SameVector:
            def encode_query(self,p):return vector
        for profile in rankings:
            ranking=reader.retrieve(spec,encoder=SameVector(),profile=profile)
            rankings[profile][qid]=[r.doc_id for r in ranking]
        original=frozen_rank(reader,prep,vector,spec.regions)
        parity['compared']+=1;parity['mismatches']+=original!=rankings['own_v6'][qid]
        times.append((time.perf_counter()-t)*1000)
    after=reader.metadata()
    if before['row_fingerprint']!=after['row_fingerprint']:raise ContractError('DATABASE_CONTENT_CHANGED')
    if parity['mismatches']:raise ContractError('LEGACY_TOP5_PARITY_FAILED')
    return dict(dataset_role='seen_dev',index_mode='EXISTING_187_READ_ONLY',index_protection_policy='legacy_v6',
                query_protection_policy='legacy_v6_query_frozen_artifact',new_evidence_v2_queries=False,
                models='REAL_PINNED_KOE5; PRECOMPUTED_FROZEN_GATE_QUERIES',rows=after['rows'],
                row_fingerprint_unchanged=True,parity=dict(parity),sidecar=coverage(side),
                latency=dict(n=len(times),per_query_three_profiles_plus_parity_p50_ms=statistics.median(times),
                             p95_ms=sorted(times)[math.ceil(.95*len(times))-1]),
                profiles={p:evaluate_rankings(reader,queries,gold,rs,states,dataset_role='seen_dev',real_model_metrics=True) for p,rs in rankings.items()},
                paid_api_calls=0)

class MemoizedRecheckEngine:
    """Volatile bounded evidence cache; config fixed by a single resident session."""
    def __init__(self,session):self.session=session;self.entries=collections.OrderedDict()
    def detect(self,text, *, input_kind):
        key=(fingerprint(text),input_kind)
        if key not in self.entries:
            self.entries[key]=self.session.infer(text,input_kind=input_kind)
            if len(self.entries)>256:self.entries.popitem(last=False)
        return self.entries[key]


def local_approve(prepared,guard,corpus_id):
    from .retrieval import ExperimentApproval
    if (prepared.state!='LOCAL_PREPARED' or not prepared.coverage_verified or prepared.protection_kind=='MASKED_ONLY'
            or prepared.sanitized_text is None):raise ContractError('LOCAL_APPROVAL_REFUSED')
    guard.check(prepared.sanitized_text)
    approved=replace(prepared,approval_state='AUTO_APPROVED',reason_codes=prepared.reason_codes+('LOCAL_EXPERIMENT_ONLY',))
    return approved,ExperimentApproval(corpus_id,fingerprint(prepared.sanitized_text),prepared.key_tag,prepared.policy_version,prepared.input_kind)


def e2e_local(python,env_file, *, queries_path=None,limit=10,profile='quality4',retrieval_profile='hybrid_v1'):
    """Real PII models + KoE5 + readonly DB + guarded local Mock. No private export."""
    from .postgres import ExistingIndexReader
    from .embeddings import KoE5Encoder
    from .protection import prepare
    from .retrieval import QuerySpec
    from .generation import PrivacyRecheck,ContextBuilder,MockTransport,MockGenerator
    from dataset_preparation import fixed_key
    from region_keys import keys_from_query
    cases=load_rows(queries_path)[:limit] if queries_path else [{'qid':'SYNTHETIC_Q','query':'교육 절차 안내'}]
    reader=ExistingIndexReader(env_file)
    before=reader.metadata();encoder=KoE5Encoder();key=fixed_key(Path(env_file))
    counters=collections.Counter();failures=collections.Counter();top5={};results=[]
    with ModelSession(python) as session:
        guard=PrivacyRecheck(MemoizedRecheckEngine(session))
        builder=ContextBuilder(guard,key_tag=reader.key_tag,policy_version='evidence_v2',index_version=reader.index_version,
                               corpus_id=reader.corpus_id,local_read_only=True)
        for case in cases:
            try:
                raw=case.get('query',case.get('question'))
                evidence=session.infer(raw,input_kind='query',profile=profile)
                plan=SnapshotGateAdapter(policy='evidence_v2').plan(raw,evidence)
                prep=prepare(raw,plan,input_kind='query',hmac_key=key,key_tag=reader.key_tag)
                if prep.state!='LOCAL_PREPARED':
                    counters[prep.state]+=1;continue
                approved,receipt=local_approve(prep,guard,reader.corpus_id)
                query=QuerySpec(approved,receipt,tuple(keys_from_query(raw,key)))
                rows=reader.retrieve(query,encoder=encoder,profile=retrieval_profile)
                top5[case['qid']]=[r.doc_id for r in rows]
                transport=MockTransport(dict(decision='ANSWER',answer='로컬 검색 문서를 확인했습니다.',citations=[rows[0].doc_id])) if rows else MockTransport(dict(decision='INSUFFICIENT_EVIDENCE',answer='근거 부족',citations=[]))
                if not rows:counters['NO_EVIDENCE']+=1;continue
                result=MockGenerator(builder,transport).generate(query,rows)
                counters['MOCK_E2E_PASS']+=1
                results.append(dict(qid=case['qid'],decision=result.decision,citation_ids=list(result.citations),subject_correctness='NOT_MEASURED',claim_support='NOT_MEASURED_MOCK',pii_output_recheck='PASS'))
            except ContractError as exc:
                counters['BLOCKED_GUARD_OR_CONTRACT']+=1;failures[str(exc)]+=1
    after=reader.metadata()
    return dict(mode='REAL_MODELS_REAL_KOE5_EXISTING_DB_LOCAL_MOCK',dataset_role='seen_dev' if queries_path else 'synthetic_query_private_index',
                index_protection_policy='legacy_v6',query_protection_policy='evidence_v2',detector_profile=profile,
                retrieval_profile=retrieval_profile,total=len(cases),counts=dict(counters),blocked_reasons=dict(failures),
                actual_top5=top5,outputs=results,fingerprint_unchanged=before['row_fingerprint']==after['row_fingerprint'],
                paid_api_calls=0,private_external_transfers=0,claim_correctness='NOT_MEASURED_MOCK_ONLY')


def isolated_synthetic_e2e(python):
    """Real-model new-text index, actual KoE5 vectors; isolated in-memory (not SQL)."""
    from .embeddings import KoE5Encoder
    from .pipeline import LocalPipeline
    from .generation import PrivacyRecheck,ContextBuilder,MockTransport,MockGenerator
    from .retrieval import InMemoryIndex,IndexedDocument,QuerySpec
    from dataset_preparation import key_tag
    key=DIAGNOSTIC_KEY;tag=key_tag(key)
    documents=[('synthetic_education','고객명: 금재원\n전화: 010-0000-0000\n교육 신청은 승인 후 완료됩니다.'),
               ('synthetic_travel','출장비 신청서는 재무 부서에 제출합니다.'),
               ('synthetic_security','보안 교육은 금요일에 진행됩니다.')]
    encoder=KoE5Encoder();counts=collections.Counter();vectors={}
    with ModelSession(python) as session:
        pipeline=LocalPipeline(session.engine('quality4'),hmac_key=key,key_tag=tag)
        guard=PrivacyRecheck(MemoizedRecheckEngine(session))
        index=InMemoryIndex(key_tag=tag,policy_version='evidence_v2',corpus_id='synthetic_new_index',index_version='isolated_memory_real_koe5_v1')
        for did,text in documents:
            outcome=pipeline.prepare(text,input_kind='document',doc_id=did)
            counts['input_documents']+=1
            try:
                p,a=local_approve(outcome.preparation,guard,index.corpus_id)
                index.add(IndexedDocument(did,p,a));vectors[did]=encoder.encode_passage(p);counts['indexed']+=1
            except ContractError:counts['excluded_gate_or_guard']+=1
        question=pipeline.prepare('교육 신청 절차를 알려주세요.',input_kind='query',doc_id='synthetic_query')
        try:
            p,a=local_approve(question.preparation,guard,index.corpus_id);q=QuerySpec(p,a)
            vec=encoder.encode_query(p)
            def scorer(masked,docs):return {d.doc_id:sum(x*y for x,y in zip(vec,vectors[d.doc_id])) for d in docs}
            rows=index.retrieve(q,dense_scorer=scorer)
            if not rows:raise ContractError('NO_INDEXABLE_EVIDENCE')
            response=dict(decision='ANSWER',answer='검색된 합성 문서를 확인했습니다.',citations=[rows[0].doc_id])
            builder=ContextBuilder(guard,key_tag=tag,policy_version=index.policy_version,index_version=index.index_version,corpus_id=index.corpus_id)
            result=MockGenerator(builder,MockTransport(response)).generate(q,rows)
            status='PASS';counts['mock_e2e_pass']+=1;citations=list(result.citations)
        except ContractError as exc:status='BLOCKED';counts[str(exc)]+=1;citations=[]
    return dict(status=status,mode='NEW_FUSION_ISOLATED_MEMORY_REAL_KOE5_MOCK_HCX',counts=dict(counts),
                index_coverage=counts['indexed']/counts['input_documents'],citation_ids=citations,
                pgvector_integration='BLOCKED_NO_VERIFIED_DISPOSABLE_DB',paid_api_calls=0,production_db_writes=0)


def synthetic_hcx_live(python,env_file, *, opt_in=False,max_calls=1,budget=0,reserve_per_call=0):
    if opt_in is not True:return dict(status='NOT_RUN_BY_POLICY',paid_api_calls=0)
    from .generation import PrivacyRecheck,ContextBuilder,LivePermit,LiveTransport,GuardedGenerator
    from .pipeline import LocalPipeline
    from .retrieval import QuerySpec
    from .contracts import RetrievedEvidence
    from dataset_preparation import key_tag
    # Only these fixed, invented no-PII fixtures can enter the live smoke helper.
    with ModelSession(python) as session:
        key=DIAGNOSTIC_KEY;tag=key_tag(key);guard=PrivacyRecheck(MemoizedRecheckEngine(session))
        pipeline=LocalPipeline(session.engine('quality4'),hmac_key=key,key_tag=tag)
        doc=pipeline.prepare('안전교육은 금요일에 진행됩니다.',input_kind='document',doc_id='synthetic_fact')
        question=pipeline.prepare('안전교육은 언제 진행되나요?',input_kind='query',doc_id='synthetic_query')
        d,_=local_approve(doc.preparation,guard,'synthetic_live_fixed')
        q,a=local_approve(question.preparation,guard,'synthetic_live_fixed');query=QuerySpec(q,a)
        rows=(RetrievedEvidence('synthetic_fact',d.sanitized_text,'none',0,False,1,('SYNTHETIC_FIXED',),
                                'synthetic_live_v1',tag,'evidence_v2',d.approval_state),)
        builder=ContextBuilder(guard,key_tag=tag,policy_version='evidence_v2',index_version='synthetic_live_v1',corpus_id='synthetic_live_fixed')
        context=builder.build(query,rows)
        permit=LivePermit(config_hash(dict(question=context.question,documents=list(context.documents))),max_calls,budget,reserve_per_call,True)
        permit.validate()
        transport=LiveTransport(env_file,guard,permit=permit)
        result=GuardedGenerator(builder,transport).generate(query,rows)
        return dict(status='HCX_LIVE_PASS',paid_api_calls=transport.calls,decision=result.decision,
                    citation_ids=list(result.citations),claim_support='NOT_AUTOMATICALLY_PROVEN',source='FIXED_SYNTHETIC_ONLY')


def e2e_compare(python,env_file,queries_path,artifact, *, limit=None):
    """Same raw queries across 3 detectors x 3 retrievers, shared raw/cache only."""
    from .postgres import ExistingIndexReader
    from .embeddings import KoE5Encoder
    from .protection import prepare
    from .retrieval import QuerySpec,evaluate_rankings
    from .generation import PrivacyRecheck,ContextBuilder,MockTransport,MockGenerator
    from dataset_preparation import fixed_key
    from region_keys import keys_from_query
    cases=load_rows(queries_path)
    if limit is not None:cases=cases[:limit]
    data=json.loads(Path(artifact).read_text())
    ids={q['qid'] for q in cases};gold=[g for g in data['gold_for_evaluation_only'] if g['qid'] in ids]
    if {g['qid'] for g in gold}!=ids:raise ContractError('GOLD_QID_MISMATCH')
    reader=ExistingIndexReader(env_file);before=reader.metadata();encoder=KoE5Encoder();key=fixed_key(Path(env_file))
    if data['hmac_key_tag']!=reader.key_tag:raise ContractError('LEGACY_ARTIFACT_KEY_MISMATCH')
    groups={p:{r:dict(counts=collections.Counter(),failures=collections.Counter(),ranked={},states={})
                  for r in ['own_v6','team_v3','hybrid_v1']} for p in ['own2','quality3','quality4']}
    vector_cache={};timings=[]
    with ModelSession(python) as session:
        guard=PrivacyRecheck(MemoizedRecheckEngine(session))
        builder=ContextBuilder(guard,key_tag=reader.key_tag,policy_version='evidence_v2',index_version=reader.index_version,
                               corpus_id=reader.corpus_id,local_read_only=True)
        for case in cases:
            qid,raw=case['qid'],case['query'];started=time.perf_counter()
            full=session.infer(raw,input_kind='query')
            for profile,retrievers in groups.items():
                evidence=subset(full,profile);plan=SnapshotGateAdapter(policy='evidence_v2').plan(raw,evidence)
                prep=prepare(raw,plan,input_kind='query',hmac_key=key,key_tag=reader.key_tag)
                if prep.state!='LOCAL_PREPARED':
                    for group in retrievers.values():group['states'][qid]=prep.state;group['counts'][prep.state]+=1
                    continue
                try:
                    approved,receipt=local_approve(prep,guard,reader.corpus_id)
                except ContractError as exc:
                    for group in retrievers.values():
                        group['states'][qid]='BLOCKED';group['counts']['QUERY_GUARD_BLOCKED']+=1;group['failures'][str(exc)]+=1
                    continue
                query=QuerySpec(approved,receipt,tuple(keys_from_query(raw,key)))
                cache_key=fingerprint(approved.sanitized_text)
                if cache_key not in vector_cache:vector_cache[cache_key]=encoder.encode_query(approved)
                vector=vector_cache[cache_key]
                class SameVector:
                    def encode_query(self,p):return vector
                for retrieval_profile,group in retrievers.items():
                    group['states'][qid]='READY'
                    try:
                        rows=reader.retrieve(query,encoder=SameVector(),profile=retrieval_profile)
                        group['ranked'][qid]=[r.doc_id for r in rows]
                        if not rows:group['counts']['NO_EVIDENCE']+=1;continue
                        transport=MockTransport(dict(decision='ANSWER',answer='로컬 검색 문서를 확인했습니다.',citations=[rows[0].doc_id]))
                        MockGenerator(builder,transport).generate(query,rows)
                        group['counts']['MOCK_E2E_PASS']+=1
                    except ContractError as exc:
                        group['counts']['CONTEXT_OR_OUTPUT_BLOCKED']+=1;group['failures'][str(exc)]+=1
            timings.append((time.perf_counter()-started)*1000)
    after=reader.metadata()
    if before['row_fingerprint']!=after['row_fingerprint']:raise ContractError('DATABASE_CONTENT_CHANGED')
    output={}
    for profile,retrievers in groups.items():
        output[profile]={}
        for name,group in retrievers.items():
            try:metrics=evaluate_rankings(reader,cases,gold,group['ranked'],group['states'],dataset_role='seen_dev',real_model_metrics=True)
            except ValueError as exc:
                if str(exc)!='NO_EVALUABLE_QUERIES':raise
                metrics=dict(status='BLOCKED_NO_EVALUABLE_QUERIES',total=len(cases))
            output[profile][name]=dict(counts=dict(group['counts']),blocked_reasons=dict(group['failures']),retrieval=metrics)
    return dict(mode='REAL_4_MODEL_CANDIDATE_REPLAY_REAL_KOE5_EXISTING_DB_MOCK_HCX',dataset_role='seen_dev',total=len(cases),
                index_protection_policy='legacy_v6',query_protection_policy='evidence_v2',
                outbound_guard='REAL_QUALITY4_MEMOIZED_SEPARATE_RECHECK',models_and_retrievers=output,
                row_fingerprint_unchanged=True,paid_api_calls=0,private_external_transfers=0,
                subject_claim_faithfulness='NOT_MEASURED_MOCK_ONLY',latency=dict(cases=len(timings),
                per_case_nine_combinations_p50_ms=statistics.median(timings),p95_ms=sorted(timings)[math.ceil(.95*len(timings))-1]))


def benchmark_profiles(python,repeats=30):
    if repeats<30:raise ContractError('WARM_SAMPLE_TOO_SMALL')
    durations={p:[] for p in ['own2','quality3','quality4','team2']}
    with ModelSession(python) as session:
        for profile in durations:
            if not session.infer(FIXTURES[0],profile=profile).ready:raise ContractError('PROFILE_MODEL_UNAVAILABLE')
        # Interleave profiles to reduce order/load bias. This is inference+IPC, not E2E.
        for _ in range(repeats):
            for profile,values in durations.items():
                started=time.perf_counter();evidence=session.infer(FIXTURES[0],profile=profile)
                if not evidence.ready:raise ContractError('PROFILE_MODEL_UNAVAILABLE')
                values.append((time.perf_counter()-started)*1000)
        resources={s:w.last_metrics for s,w in session.workers.items()}
        peak=session.sampled_child_rss_peak_mb
    return dict(mode='REAL_MODELS_FIXED_SYNTHETIC_INTERLEAVED',same_input=True,repeats=repeats,
                timing_scope='RAW_INFERENCE_PLUS_STDIO_AND_PROCESS_RSS_SAMPLING',all_workers_resident=True,
                profiles={p:dict(p50_ms=statistics.median(v),p95_ms=sorted(v)[math.ceil(.95*len(v))-1]) for p,v in durations.items()},
                shared_worker_resources=resources,sampled_child_rss_peak_mb=peak,paid_api_calls=0)
