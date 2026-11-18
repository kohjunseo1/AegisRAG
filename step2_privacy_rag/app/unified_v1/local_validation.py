"""Explicit offline, fabricated-only live model smoke. PII-free JSON summaries."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from .adapters import PersistentPredictor
from .contracts import config_hash, PROFILES, DetectionEvidence
from .runtime import KLUE_HASHES, build_engine, benchmark_predictor, profile_status
from .replay import SnapshotGateAdapter

FIXTURES=('고객명: 금재원\n전화: 010-0000-0000','선우 님 합성 안내','탁 윤 씨 이메일 fixture@example.invalid',
          '남궁수는 합성 문서를 확인했다.','😀금 재원에게 합성 안내를 전달했다.')

def run_local_smoke(python, *, repeats=30):
    worker=Path(__file__).with_name('worker.py').resolve()
    root=Path.home()/'private-models/aegisrag'
    manifests={'own2':dict(source_id='own2',source_revision='frozen-v6'),
               'klue':dict(source_id='klue',source_revision=config_hash(KLUE_HASHES),model_dir=str(root/'roberta_small_v1'),
                           trusted_root=str(root),hashes=KLUE_HASHES,max_length=512,stride=64)}
    output={'mode':'LOCAL_MODEL_SYNTHETIC','models':{},'profiles':{},'paid_api_calls':0}
    with tempfile.TemporaryDirectory(prefix='aegis-followup-manifests-') as temp:
        workers={}
        try:
            for source,manifest in manifests.items():
                path=Path(temp)/(source+'.json');path.write_text(json.dumps(manifest));path.chmod(0o600)
                worker_client=PersistentPredictor([str(Path(python).absolute()),str(worker),str(path)],config_hash(manifest),timeout=120)
                workers[source]=worker_client
                try:
                    counts=[]
                    for text in FIXTURES:
                        rows=worker_client(text)
                        counts.append({s:len(r) for s,r in rows.items()} if isinstance(rows,dict) else len(rows))
                    output['models'][source]=dict(status='LOCAL_MODEL_PASS',fixture_count=len(FIXTURES),
                                                   candidate_counts=counts,benchmark=benchmark_predictor(worker_client,FIXTURES[0],repeats=repeats))
                except Exception as exc:
                    from .contracts import ContractError
                    output['models'][source]=dict(status='BLOCKED',reason=str(exc) if isinstance(exc,ContractError) else type(exc).__name__)
            validated={}
            if output['models']['own2']['status']=='LOCAL_MODEL_PASS':validated.update(ax='LOCAL_MODEL_PASS',veil='LOCAL_MODEL_PASS')
            if output['models']['klue']['status']=='LOCAL_MODEL_PASS':validated['klue']='LOCAL_MODEL_PASS'
            for profile in PROFILES:output['profiles'][profile]=profile_status(profile,validated)
            if output['profiles']['quality3']=='REAL_READY':
                engine=build_engine('quality3',own_worker=workers['own2'],klue_worker=workers['klue'])
                gate=SnapshotGateAdapter(policy='evidence_v2')
                output['three_model_smoke']=[]
                snapshots=[]
                for text in FIXTURES:
                    ev=engine.detect(text,input_kind='document');plan=gate.plan(text,ev)
                    snapshots.append((text,ev))
                    output['three_model_smoke'].append(dict(model_status=dict(ev.model_status_by_source),
                                                           protection_status=plan.protection_status,mask_count=len(plan.mask_intervals)))
                output['synthetic_ablation']=evaluate_snapshot_ablation(snapshots)
        finally:
            for w in workers.values():w.close()
    return output

def run_db_smoke(env_file):
    from .postgres import ExistingIndexReader
    from .embeddings import KoE5Encoder
    from .contracts import PreparationResult
    from .retrieval import approve_experiment,QuerySpec
    from hcx import redact_residual_pii
    reader=ExistingIndexReader(env_file)
    before=reader.metadata()
    encoder=KoE5Encoder()
    prepared=PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED','교육 절차 안내',reader.key_tag,
                               reader.policy_version,'query',coverage_verified=True)
    query,receipt=approve_experiment(prepared,corpus_id=reader.corpus_id,
                                    checker=lambda t:not any(redact_residual_pii(t)[1].values()))
    spec=QuerySpec(query,receipt)
    vector=encoder.encode_query(query)
    class ReuseVector:
        def encode_query(self,p):return vector
    rankings={profile:[r.doc_id for r in reader.retrieve(spec,profile=profile,encoder=ReuseVector())]
              for profile in ['own_v6','team_v3','hybrid_v1']}
    # Frozen V6 retrieval, identical prepared query/vector/187 rows; no new embedding.
    from core import LivePipeline,PreparedQuery
    from settings import table_identifier
    old=LivePipeline.__new__(LivePipeline)
    old._encode=lambda t:'['+','.join(format(float(v),'.9g') for v in vector)+']'
    old.db=reader._db;old.table=table_identifier('rag.aegis_live_docs');old.tag=reader.key_tag
    old.index_mode='live';old.expected_rows=187;old.timings={}
    legacy=[r['doc_id'] for r in old.retrieve(PreparedQuery(query.sanitized_text,set(),set()))]
    after=reader.metadata()
    if before['row_fingerprint']!=after['row_fingerprint']:raise RuntimeError('DB_CHANGED')
    return dict(status='DB_READ_ONLY_SEARCH_PASS',rows=before['rows'],key_matches=True,
                fingerprint_unchanged=True,token_gin_present=before['token_gin_present'],
                query_preprocessing='KNOWN_SYNTHETIC_FIXTURE',embedding='REAL_PINNED_KOE5',
                original_v6_top5_parity=rankings['own_v6']==legacy,actual_top5=rankings,
                quality_metrics='NOT_MEASURED_NO_GOLD',paid_api_calls=0)

def klue_parity_probe(reference_dir):
    """Reference defs executed in this isolated process only; no source modification."""
    import ast
    import torch
    from .adapters import LocalHFPredictor
    p=LocalHFPredictor(str(Path.home()/'private-models/aegisrag/roberta_small_v1'),
                       str(Path.home()/'private-models/aegisrag'),KLUE_HASHES)
    source=Path(reference_dir)/'starter/pii_pipeline.py'
    tree=ast.parse(source.read_text())
    wanted={'_chunks','_tags_to_spans','model_predict'}
    module=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted],type_ignores=[])
    namespace={'_load_model':lambda:(p.tok,p.model)}
    exec(compile(module,str(source),'exec'),namespace)
    result=dict(fixture_count=len(FIXTURES),raw_decoder_parity=True,original_runtime_parity='NOT_RUN',weights='LOCAL_HASH_VERIFIED')
    try:
        namespace['model_predict'](FIXTURES[0]);result['original_runtime_parity']='PASS'
    except AttributeError:result['original_runtime_parity']='BLOCKED_ORIGINAL_LOGITS_ACCESS_RETURN_DICT_FALSE'
    for text in FIXTURES:
        enc=p.tok(text,return_offsets_mapping=True,truncation=True,max_length=512,return_tensors='pt')
        offsets=enc.pop('offset_mapping')[0].tolist();enc.pop('token_type_ids',None)
        with torch.inference_mode():out=p.model(**enc)
        logits=out.logits if hasattr(out,'logits') else out[0]
        tags=[p.model.config.id2label[i] for i in logits[0].argmax(-1).tolist()]
        old=namespace['_tags_to_spans'](offsets,tags)
        new=[{k:r[k] for k in ('label','start','end')} for r in p(text)]
        if old!=new:result['raw_decoder_parity']=False
    long='합성 '*600+'금재원'
    enc=p.tok(long,return_offsets_mapping=True,return_overflowing_tokens=True,truncation=True,max_length=512,stride=64)
    result['long_line_window_count']=len(enc['offset_mapping'])
    result['long_line_tail_covered']=max(end for win in enc['offset_mapping'] for start,end in win)==len(long)
    return result


def evaluate_snapshot_ablation(snapshots):
    from .evaluation import protection_metrics
    annotations=[(('person','금재원'),('phone_number','010-0000-0000')),
                 (('person','선우'),),(('person','탁 윤'),('email','fixture@example.invalid')),
                 (('person','남궁수'),),(('person','금 재원'),)]
    output={}
    for profile in ['own2','quality3','ax_klue','veil_klue','drop_koelectra']:
        rows=[]
        for (text,evidence),labels in zip(snapshots,annotations):
            sources=PROFILES[profile]
            subset=DetectionEvidence(evidence.input_kind,evidence.request_id,
                                      {s:evidence.raw_candidates_by_source[s] for s in sources},
                                      {s:evidence.model_status_by_source[s] for s in sources},(),
                                      config_hash({'source_config':evidence.config_hash,'profile':profile}),
                                      evidence.original_input_fingerprint,profile)
            plan=SnapshotGateAdapter(policy='evidence_v2').plan(text,subset)
            gold=[dict(label=label,start=text.index(value),end=text.index(value)+len(value)) for label,value in labels]
            rows.append(protection_metrics(plan,gold))
        tp=sum(r['gold_entities']-r['strict_fn'] for r in rows)
        fp=sum(r['strict_fp'] for r in rows);fn=sum(r['strict_fn'] for r in rows)
        protected=sum(r['protected_gold_chars'] for r in rows);chars=sum(r['gold_chars'] for r in rows)
        output[profile]=dict(dataset_role='synthetic',real_model_candidates=True,timing_mode='SAME_RAW_CANDIDATE_REPLAY',
                             fixtures=len(rows),strict_micro_f1=2*tp/(2*tp+fp+fn) if tp+fp+fn else 1,
                             strict_fp=fp,strict_fn=fn,character_protection_rate=protected/chars,
                             completely_missed=sum(r['completely_missed'] for r in rows),
                             gate_counts={s:sum(r['gate_state']==s for r in rows) for s in ['READY','REVIEW','BLOCKED']})
    return output
