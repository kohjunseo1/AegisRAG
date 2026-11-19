"""Local selection experiments. Gold is consumed only by scorers, never policies.

Run with the compatible model Python and PYTHONPATH=step2_privacy_rag/app.
Raw snapshots are mode 0600 outside the repository; committed outputs are aggregates.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from .contracts import Candidate, DetectionEvidence, LABELS, fingerprint, config_hash
from .experiments import ModelSession, subset, load_rows, finish_counts, DIAGNOSTIC_KEY
from .replay import SnapshotGateAdapter
from .protection import merge_intervals, covers, StructuralSentinel, ContextualBoost

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'reports/unified_v1/selection'
PRIVATE=Path.home()/'.cache/aegisrag/unified_v1/private/selection'
DATA=Path('/Users/gojunseo/AegisRAG/step2_privacy_rag/local_eval')
A=DATA/'holdout_labeled.jsonl'
B=Path.home()/'Downloads/blind_eval.jsonl'
REFERENCE=Path('/Users/gojunseo/privacy-rag-chatbot-reference')
SOURCES=('ax','veil','klue','koelectra')
POLICY_VERSION='selection-v1-20261119'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path,value,private=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if private:path.parent.chmod(0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600 if private else 0o644)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    if private:path.chmod(0o600)

def write_csv(name,rows):
    OUT.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w') as f:
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");writer.writeheader();writer.writerows(rows)

def summary_ms(values):
    return dict(n=len(values),p50_ms=statistics.median(values),p95_ms=sorted(values)[max(0,int(len(values)*.95+.999)-1)]) if values else {}

def manifest():
    from .runtime import KLUE_HASHES, KOELECTRA_HASHES
    sets={};texts={}
    for name,path in [('A',A),('B',B)]:
        rows=load_rows(path);fps=[];lengths=[];labels=Counter()
        for r in rows:
            text=r['text'];lengths.append(len(text));fps.append(fingerprint(text))
            assert all(e['label'] in LABELS and 0<=e['start']<e['end']<=len(text) and text[e['start']:e['end']]==e['text'] for e in r['entities'])
            assert len({(e['label'],e['start'],e['end']) for e in r['entities']})==len(r['entities'])
            labels.update(e['label'] for e in r['entities'])
        assert len({r['id'] for r in rows})==len(rows)
        sets[name]=dict(path=str(path),sha256=digest(path),documents=len(rows),gold=sum(labels.values()),
                       labels=dict(labels),duplicate_texts=len(fps)-len(set(fps)),length_min=min(lengths),
                       length_median=statistics.median(lengths),length_max=max(lengths),
                       role='seen_dev' if name=='A' else 'previously_exposed_distribution_shift_validation')
        texts[name]=set(fps)
    assert sets['B']['sha256']=='52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f'
    git=lambda p:subprocess.check_output(['git','rev-parse','HEAD'],cwd=p,text=True).strip()
    from importlib.metadata import version,PackageNotFoundError
    versions={}
    for lib in ['torch','transformers','tokenizers','huggingface-hub','numpy','kiwipiepy']:
        try:versions[lib]=version(lib)
        except PackageNotFoundError:versions[lib]='MISSING'
    result=dict(version=POLICY_VERSION,code_head=git(ROOT),reference_head=git(REFERENCE),datasets=sets,
                cross_dataset_exact_overlap=len(texts['A']&texts['B']),environment=versions,
                model_hashes=dict(klue=KLUE_HASHES,koelectra=KOELECTRA_HASHES),
                scorer_sha256=digest(__file__),scorer='micro exact label+Unicode[start,end); set union; whole-entity mask coverage separately',
                ablation_thresholds=dict(tau_accept=.9,tau_review=.5,tau_span=.35,tau_cand=.15),
                gold_is_evaluation_only=True,paid_calls=0,sql_writes=0,
                historical_results='final_summary.json is exposed prior evidence, not an independent blind result')
    write_json(OUT/'EXPERIMENT_MANIFEST.json',result);return result

def pack(ev):
    return dict(input_kind=ev.input_kind,request_id=ev.request_id,
                raw_candidates_by_source={s:[asdict(c) for c in rows] for s,rows in ev.raw_candidates_by_source.items()},
                model_status_by_source=dict(ev.model_status_by_source),structural_candidates=[],
                config_hash=ev.config_hash,original_input_fingerprint=ev.original_input_fingerprint,detector_profile=ev.detector_profile)

def unpack(obj):
    obj=dict(obj);obj['raw_candidates_by_source']={s:tuple(Candidate(**c) for c in rows) for s,rows in obj['raw_candidates_by_source'].items()}
    return DetectionEvidence(**obj)

def snapshot(session,text,profile='quality4'):
    signature=config_hash(dict(version=POLICY_VERSION,model_contract='01313ab-local-pinned',profile=profile,text=fingerprint(text)))
    path=PRIVATE/'raw'/f'{signature}.json'
    if path.exists():
        obj=json.loads(path.read_text());ev=unpack(obj['evidence'])
        assert ev.original_input_fingerprint==fingerprint(text) and ev.ready
        return ev,obj['metrics'],True
    started=time.perf_counter();ev=session.infer(text,profile=profile)
    if not ev.ready:raise RuntimeError('RAW_MODEL_UNAVAILABLE')
    metrics=dict(wall_ms=(time.perf_counter()-started)*1000,
                 workers={s:dict(w.last_metrics) for s,w in session.workers.items() if w.last_metrics})
    write_json(path,dict(evidence=pack(ev),metrics=metrics),True)
    return ev,metrics,False

def raw_score(pred,gold):
    c=Counter(tp=len(pred&gold),fp=len(pred-gold),fn=len(gold-pred))
    c['overlap_gold']=sum(any(a<d and b>c0 for _,c0,d in pred) for _,a,b in gold)
    c['boundary_only']=sum(g not in pred and any(l==g[0] and a<g[2] and b>g[1] for l,a,b in pred) for g in gold)
    c['fully_missed']=len(gold)-c['overlap_gold']
    c['type_conflicts']=sum(any(a==c0 and b==d and l!=k for k,c0,d in pred) for l,a,b in gold)
    return c

def raw_experiment(python):
    combos=[c for n in range(1,5) for c in itertools.combinations(SOURCES,n)]
    totals={c:Counter() for c in combos};labels=defaultdict(Counter);timings=defaultdict(list);agreement=Counter();paired=[];cached=0
    with ModelSession(python) as session:
        for number,r in enumerate(load_rows(A),1):
            ev,metrics,hit=snapshot(session,r['text']);cached+=hit
            gold={(e['label'],e['start'],e['end']) for e in r['entities']}
            per={s:{(c.label,c.start,c.end) for c in rows if c.label in LABELS} for s,rows in ev.raw_candidates_by_source.items()}
            for c in combos:
                started=time.perf_counter();pred=set().union(*(per[s] for s in c));v=raw_score(pred,gold);totals[c].update(v)
                timings['replay_'+','.join(c)].append((time.perf_counter()-started)*1000)
                for label in sorted(LABELS):labels[(c,label)].update(raw_score({x for x in pred if x[0]==label},{x for x in gold if x[0]==label}))
                paired.append(dict(doc_id=r['id'],models='+'.join(c),**dict(v)))
            own=per['ax']|per['veil']
            for s in SOURCES:
                agreement[s+'_unique_tp_vs_other3']+=len((per[s]-set().union(*(per[t] for t in SOURCES if t!=s)))&gold)
                agreement[s+'_additional_tp_vs_own2']+=len((per[s]-own)&gold)
                agreement[s+'_additional_fp_vs_own2']+=len((per[s]-own)-gold)
            for s,t in itertools.combinations(SOURCES,2):agreement[s+'+'+t+'_exact_agreement']+=len(per[s]&per[t])
            for s,m in metrics['workers'].items():timings[s].append(m['inference_ms'])
            if number%20==0:print(json.dumps(dict(phase='P1',documents=number,cache_hits=cached)),flush=True)
    rows=[]
    for c,v in totals.items():rows.append(dict(dataset='A',mode='REAL_MODEL_RAW',models='+'.join(c),documents=187,**finish_counts(v),
                                              **{k:v[k] for k in ['overlap_gold','boundary_only','fully_missed','type_conflicts']},
                                              replay_p95_ms=summary_ms(timings['replay_'+','.join(c)])['p95_ms']))
    write_csv('step1_raw.csv',rows)
    write_csv('step1_raw_by_label.csv',[dict(models='+'.join(c),label=l,**finish_counts(v)) for (c,l),v in labels.items()])
    write_csv('step1_raw_paired.csv',paired)
    write_json(OUT/'raw_resources.json',dict(corpus_worker_timing={s:summary_ms(timings[s]) for s in ['own2','klue','koelectra']},
        agreement=dict(agreement),snapshot_cache_hits=cached,notes='own2 worker measures AX+Veil jointly; union replay costs exclude model inference; first request includes load only in wall_ms'))
    return rows

def frozen_outcome(text,ev):
    gate=SnapshotGateAdapter().check(text,subset(ev,'own2'))
    # The final frozen prepare is authoritative: a pseudonymizer error can turn
    # an intermediate Gate REVIEW into BLOCKED (notably malformed identifiers).
    from document_preparation import prepare as original_prepare
    from .replay import SnapshotDetector
    with SnapshotDetector(text,subset(ev,'own2')) as detector:
        actual=original_prepare('DOC_SELECTION',text,detector,DIAGNOSTIC_KEY)
    masks=[(s['start'],s['end']) for s in gate.get('mask_intervals',[])+gate.get('proposed_protection',[])]
    return dict(pred={(s['label'],s['start'],s['end']) for s in gate.get('entities',[])},
                hmac={(s['label'],s['start'],s['end']) for s in gate.get('entities_for_pseudonymization',[])},
                masks=merge_intervals(masks,len(text)),ready=actual.state=='LOCAL_PREPARED',
                blocked=actual.state=='BLOCKED',gate=gate,pending=0)

def fusion_outcome(text,ev,policy,teacher=None):
    base=frozen_outcome(text,ev)
    if policy=='F0':return base
    if policy=='F1':
        plan=SnapshotGateAdapter(policy='evidence_v2').plan(text,ev)
        return dict(pred={(c.label,c.start,c.end) for c in plan.strict_entities},hmac={(c.label,c.start,c.end) for c in plan.entities_for_hmac},
                    masks=plan.mask_intervals,ready=not plan.review_reason_codes and not plan.pending_mask_intervals,
                    blocked=plan.protection_status=='BLOCKED',pending=len(plan.pending_mask_intervals))
    if policy=='F2':
        spans=teacher['spans'];review=teacher['review']
        return dict(pred={(s['label'],s['start'],s['end']) for s in spans},hmac=set(),
                    masks=merge_intervals([(s['start'],s['end']) for s in spans],len(text)),ready=False,blocked=False,pending=len(spans),
                    note='original detector only; original 32-bit HMAC never adopted; review not automatically masked')
    if policy=='F5':
        # Gold-free triggers; explicitly cannot detect marker-free misses.
        trigger=not base['ready'] or any(not covers((c.start,c.end),base['masks']) for c in StructuralSentinel().candidates(text))
        if not trigger:return dict(base,cascade_trigger=False)
        out=fusion_outcome(text,ev,'F4');out['cascade_trigger']=True;return out
    if policy=='F3':
        candidates=[(s['label'],s['start'],s['end']) for s in teacher['spans']+teacher['review']]
    elif policy=='F4':
        # Typed proposals: use model agreement for names/addresses, format validators
        # for numbers. They remain pending, never become HMAC solely by consensus.
        support=Counter((c.label,c.start,c.end) for rows in ev.raw_candidates_by_source.values() for c in rows)
        sent={(c.label,c.start,c.end) for c in StructuralSentinel().candidates(text)}
        candidates=[t for t,n in support.items() if n>=2 or t in sent or t[0].startswith('extra_sensitive:')]
        candidates+=list(sent)
    else:raise ValueError('UNKNOWN_POLICY')
    extra=[(l,a,b) for l,a,b in candidates if not covers((a,b),base['masks']) or any(a<d and b>c and l!=k for k,c,d in base['hmac'])]
    return dict(base,masks=merge_intervals(list(base['masks'])+[(a,b) for _,a,b in extra],len(text)),
                ready=base['ready'] and not extra,pending=len(extra))

class ConditionalCascade:
    """Actual Gold-free dispatch. No auxiliary inference on an untriggered input.

    Missing auxiliary heads on a triggered request raise; they never fall back to
    claiming the baseline safe. This experimental policy is not the selected default.
    """
    def __init__(self,session):self.session=session

    def run(self,text):
        from .adapters import RawAdapter
        from .runtime import KLUE_HASHES,KOELECTRA_HASHES
        ev=self.session.infer(text,profile='own2');base=frozen_outcome(text,ev)
        trigger=not base['ready'] or any(not covers((c.start,c.end),base['masks']) for c in StructuralSentinel().candidates(text))
        if not trigger:return dict(base,cascade_trigger=False)
        rows=dict(ev.raw_candidates_by_source);statuses=dict(ev.model_status_by_source)
        for source,hashes in [('klue',KLUE_HASHES),('koelectra',KOELECTRA_HASHES)]:
            rows[source]=RawAdapter(source,config_hash(hashes),self.session.workers[source]).predict(text)
            statuses[source]='OK'
        full=DetectionEvidence(ev.input_kind,ev.request_id,rows,statuses,(),config_hash({'cascade':POLICY_VERSION}),fingerprint(text),'quality4')
        return dict(fusion_outcome(text,full,'F4'),cascade_trigger=True)

def cascade_benchmark(python):
    timings=defaultdict(list);rows=[];counts=Counter()
    with ModelSession(python) as session:
        session.infer('합성 업무 절차 안내') # All workers warm before interleaved corpus measurements.
        cascade=ConditionalCascade(session)
        for number,r in enumerate(load_rows(A),1):
            text=r['text'];reference,_,_=snapshot(session,text)
            expected=fusion_outcome(text,reference,'F5')
            for name in ('F0','F5','always_quality4')[number%3:]+('F0','F5','always_quality4')[:number%3]:
                started=time.perf_counter()
                if name=='F5':out=cascade.run(text)
                else:
                    ev=session.infer(text,profile='own2' if name=='F0' else 'quality4')
                    out=frozen_outcome(text,ev) if name=='F0' else fusion_outcome(text,ev,'F1')
                ms=(time.perf_counter()-started)*1000;timings[name].append(ms)
                rows.append(dict(doc_id=r['id'],policy=name,latency_ms=ms,trigger=out.get('cascade_trigger',False)))
                if name=='F5':
                    counts['triggered']+=out['cascade_trigger']
                    counts['parity_mismatches']+=any(out[k]!=expected[k] for k in ['pred','hmac','masks','ready','blocked','pending'])
            if number%30==0:print(json.dumps(dict(phase='CASCADE_COST',documents=number)),flush=True)
    write_csv('cascade_latency_paired.csv',rows)
    write_json(OUT/'cascade_benchmark.json',dict(mode='REAL_MODELS_INTERLEAVED_CORPUS',documents=187,counts=dict(counts),
        profiles={n:dict(**summary_ms(v),documents_per_second=1000/statistics.mean(v)) for n,v in timings.items()},
        scope='additional benchmark runs, not original P1 snapshots; cold excluded, all workers resident, order rotated per document'))
    if counts['parity_mismatches']:raise RuntimeError('CASCADE_REPLAY_PARITY_FAILED')

def score_fusion(out,gold):
    c=raw_score(out['pred'],gold);c['gold']=len(gold)
    c['whole_protected']=sum(covers((a,b),out['masks']) for _,a,b in gold)
    c['mask_completely_missed']=sum(not any(a<d and b>c0 for c0,d in out['masks']) for _,a,b in gold)
    chars={i for _,a,b in gold for i in range(a,b)};protected={i for a,b in out['masks'] for i in range(a,b)}
    c['gold_characters']=len(chars);c['protected_characters']=len(chars&protected)
    c['READY' if out['ready'] else 'BLOCKED' if out['blocked'] else 'REVIEW']+=1
    c['hmac_tp']=len(out['hmac']&gold);c['hmac_fp']=len(out['hmac']-gold)
    c['pending_candidates']=out['pending'];c['cascade_triggers']=out.get('cascade_trigger',False)
    return c

def teacher_experiment(python,rows,option='default'):
    cache=PRIVATE/'teacher'/f'{digest(A)}-{option}.json'
    if cache.exists():return json.loads(cache.read_text())
    import sys
    worker=Path(__file__).with_name('selection_teacher.py')
    env={'PATH':os.environ.get('PATH',''),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    with subprocess.Popen([python,str(worker),str(REFERENCE),option],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env) as p:
        result={}
        for n,r in enumerate(rows,1):
            p.stdin.write(json.dumps(dict(text=r['text']))+'\n');p.stdin.flush()
            line=p.stdout.readline()
            if not line:raise RuntimeError('TEACHER_WORKER_FAILED')
            obj=json.loads(line)
            if obj.get('error'):raise RuntimeError(obj['error'])
            result[fingerprint(r['text'])]=obj
            if n%30==0:print(json.dumps(dict(phase='P2',teacher=option,documents=n)),flush=True)
        p.stdin.close();p.wait(timeout=15)
    write_json(cache,result,True);return result

def fusion_experiment(python):
    rows=load_rows(A)
    options=('default','no_ensemble','no_boost','drop_unagreed','num_union','name_loose','name_none','with_kiwi')
    teachers={};ablation=[]
    for option in options:
        teachers[option]=teacher_experiment(python,rows,option)
        count=Counter();times=[]
        for r in rows:
            obj=teachers[option][fingerprint(r['text'])];gold={(e['label'],e['start'],e['end']) for e in r['entities']}
            count.update(raw_score({(s['label'],s['start'],s['end']) for s in obj['spans']},gold));times.append(obj['inference_ms'])
        ablation.append(dict(option=option,**finish_counts(count),**summary_ms(times),review_candidates=sum(len(o['review']) for o in teachers[option].values())))
    write_csv('team_original_ablation.csv',ablation)
    totals={p:Counter() for p in ['F0','F1','F2','F3','F4','F5']};times=defaultdict(list);paired=[]
    with ModelSession(python) as session:
        for r in rows:
            ev,_,_=snapshot(session,r['text']);gold={(e['label'],e['start'],e['end']) for e in r['entities']}
            for p,c in totals.items():
                t=time.perf_counter();o=fusion_outcome(r['text'],ev,p,teachers['default'][fingerprint(r['text'])]);times[p].append((time.perf_counter()-t)*1000)
                score=score_fusion(o,gold);c.update(score);paired.append(dict(dataset='A',doc_id=r['id'],policy=p,**dict(score)))
    output=[]
    for p,c in totals.items():output.append(dict(dataset='A',policy=p,mode='REAL_MODEL_FUSION_SNAPSHOT_REPLAY',**finish_counts(c),
        **{k:c[k] for k in ['gold','whole_protected','mask_completely_missed','READY','REVIEW','BLOCKED','hmac_tp','hmac_fp','pending_candidates','cascade_triggers']},
        character_protection=c['protected_characters']/c['gold_characters'],replay_p95_ms=summary_ms(times[p])['p95_ms']))
    # Conservative Pareto gate: keep only measured improvements without coverage loss.
    f0=output[0];selected=['F0']
    for r in output[1:]:
        if r['policy']=='F2':continue # detector-only, incompatible original HMAC
        if (r['whole_protected']>=f0['whole_protected'] and r['mask_completely_missed']<=f0['mask_completely_missed']
                and r['READY']>=f0['READY'] and r['hmac_fp']<=f0['hmac_fp'] and r['f1']>f0['f1']):selected.append(r['policy'])
    selected=selected[:2]
    lock=OUT/'SELECTION_LOCK.json'
    if lock.exists():
        if json.loads(lock.read_text())['policies']!=selected:raise RuntimeError('LOCKED_SELECTION_CHANGED')
    else:
        write_json(lock,dict(policies=selected,version=POLICY_VERSION,scorer_sha256=digest(__file__),
            before_B_performance=True,reason='A: no safety/READY regression plus strict F1 improvement; otherwise frozen control',thresholds='fixed by roadmap'))
    # Only frozen policies and control are evaluated on B. No B tuning.
    btot={p:Counter() for p in selected};blabel=defaultdict(Counter)
    with ModelSession(python) as session:
        for n,r in enumerate(load_rows(B),1):
            ev,_,_=snapshot(session,r['text'],profile='own2' if selected==['F0'] else 'quality4')
            gold={(e['label'],e['start'],e['end']) for e in r['entities']}
            for p in selected:
                o=fusion_outcome(r['text'],ev,p);score=score_fusion(o,gold);btot[p].update(score)
                paired.append(dict(dataset='B',doc_id=r['id'],policy=p,**dict(score)))
                for l in sorted(LABELS):blabel[(p,l)].update(raw_score({x for x in o['pred'] if x[0]==l},{x for x in gold if x[0]==l}))
            if n%30==0:print(json.dumps(dict(phase='P2',validation_B=n)),flush=True)
    for p,c in btot.items():output.append(dict(dataset='B',policy=p,mode='REAL_MODEL_FUSION_FROZEN_VALIDATION',**finish_counts(c),
        **{k:c[k] for k in ['gold','whole_protected','mask_completely_missed','READY','REVIEW','BLOCKED','hmac_tp','hmac_fp','pending_candidates']},
        character_protection=c['protected_characters']/c['gold_characters']))
    write_csv('step1_fusion.csv',output);write_csv('step1_fusion_paired.csv',paired)
    write_csv('validation_B_labels.csv',[dict(policy=p,label=l,**finish_counts(c)) for (p,l),c in blabel.items()])
    return output

def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['P0','P1','P2']);parser.add_argument('--runtime-python',default=os.sys.executable)
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--diagnostics-only',action='store_true')
    parser.add_argument('--cascade-benchmark',action='store_true')
    args=parser.parse_args()
    if args.audit_only:audit_experiment(args.runtime_python);return
    if args.diagnostics_only:fusion_diagnostics();return
    if args.cascade_benchmark:cascade_benchmark(args.runtime_python);return
    if args.phase=='P0':manifest();print('P0 PASS')
    elif args.phase=='P1':raw_experiment(args.runtime_python);print('P1 PASS')
    elif args.phase=='P2':
        fusion_experiment(args.runtime_python);audit_experiment(args.runtime_python);fusion_diagnostics();print('P2 PASS')

def audit_experiment(python):
    """Parity with actual frozen heads, resource measurements and original replay.

    Additional runs are explicitly validation/benchmark, not P1 snapshots.
    """
    from local_pseudonymizer import pseudonymize_local
    parity=Counter();samples=defaultdict(list);resources={};own_snapshots=[]
    with ModelSession(python) as session:
        for n,r in enumerate(load_rows(A),1):
            ev,m,_=snapshot(session,r['text']);own_snapshots.append(m)
            gate=frozen_outcome(r['text'],ev)['gate']
            session.workers['own2'](r['text'],diagnostic_legacy=True)
            diag=session.workers['own2'].last_diagnostics
            state='LOCAL_PREPARED' if gate.get('decision')=='READY_FOR_LOCAL_PSEUDONYMIZATION' else 'BLOCKED' if gate.get('decision')=='BLOCKED' else 'REVIEW'
            parity['documents']+=1;parity['state_mismatches']+=state!=diag['state']
            if state==diag['state']=='LOCAL_PREPARED':
                masked=pseudonymize_local(r['text'],gate,hmac_key=DIAGNOSTIC_KEY).sanitized_text
                parity['hmac_compared']+=1;parity['hmac_mismatches']+=fingerprint(masked)!=diag['sanitized_fingerprint']
            if n%40==0:print(json.dumps(dict(phase='P2_PARITY',documents=n)),flush=True)
        for source in SOURCES:
            worker=session.workers['own2' if source in ('ax','veil') else source]
            kwargs=dict(sources=[source]) if source in ('ax','veil') else {}
            worker('합성 업무 절차 안내',**kwargs)
            for _ in range(30):
                t=time.perf_counter();worker('합성 업무 절차 안내',**kwargs);samples[source].append((time.perf_counter()-t)*1000)
            resources[source]=dict(worker.last_metrics)
    if parity['state_mismatches'] or parity['hmac_mismatches']:raise RuntimeError('FROZEN_REGRESSION')
    write_json(OUT/'frozen_parity_and_cost.json',dict(actual_frozen_parity=dict(parity),
        warm_fixed_synthetic={s:dict(**summary_ms(v),throughput_per_second=1000/statistics.mean(v)) for s,v in samples.items()},
        worker_resources=resources,quality4_corpus_wall=summary_ms([m['wall_ms'] for m in own_snapshots]),
        shared_resident_workers=True,benchmark_scope='30 warm requests/source, isolated single-source calls; corpus inference on A separately',
        cold_load='worker_resources cold_load_ms; cold path includes full own2 loader for AX and Veil',
        cascade_cost='NOT_MEASURED actual conditional scheduling; F5 protection is replay only'))
    manifest_path=OUT/'EXPERIMENT_MANIFEST.json';m=json.loads(manifest_path.read_text())
    m['own_frozen_model_manifest']=json.loads((ROOT/'step1_pii_detector/src/pii_detector/model_manifest.json').read_text())
    m['evaluation_code_final_sha256']=digest(__file__)
    m['datasets']['C']=dict(queries=88,documents=187,queries_sha256=digest(DATA/'holdout_retrieval_queries.jsonl'),
        gold_sha256=digest(DATA/'holdout_retrieval_queries_gold.jsonl'),artifact_sha256=digest(DATA/'koe5_v6_private/sanitized_eval.json'))
    write_json(manifest_path,m)
    rows=[]
    for option in ('default','no_ensemble','no_boost','drop_unagreed','num_union','name_loose','name_none','with_kiwi'):
        objs=json.loads((PRIVATE/'teacher'/f'{digest(A)}-{option}.json').read_text()).values()
        costs=[o['inference_ms'] for o in objs if o.get('raw_snapshot_reused')]
        actual=[o['inference_ms'] for o in objs if not o.get('raw_snapshot_reused')]
        rows.append(dict(option=option,replay_requests=len(costs),actual_inference_requests=len(actual),
                         replay_p95_ms=summary_ms(costs).get('p95_ms'),actual_inference_plus_policy_p95_ms=summary_ms(actual).get('p95_ms')))
    write_csv('team_timing_scope.csv',rows)
    print('P2 AUDIT PASS',flush=True)

def fusion_diagnostics():
    """Scoring-only detailed state/coverage summaries. Never changes policies."""
    paired=list(csv.DictReader((OUT/'step1_fusion_paired.csv').open()));scopes=[]
    for dataset,policy in sorted({(r['dataset'],r['policy']) for r in paired}):
        selected=[r for r in paired if r['dataset']==dataset and r['policy']==policy]
        ready=[r for r in selected if int(r.get('READY') or 0)]
        gold=sum(int(r['gold']) for r in ready);whole=sum(int(r['whole_protected']) for r in ready)
        scopes.append(dict(dataset=dataset,policy=policy,READY_documents=len(ready),READY_gold=gold,READY_whole_protected=whole,
                           READY_uncovered_gold=gold-whole,scope='Gold coverage of emitted READY plans, not proof of unknown-PII absence'))
    write_csv('ready_safety_scope.csv',scopes)
    from .replay import SnapshotDetector
    from document_preparation import prepare as original_prepare
    from local_pseudonymizer import pseudonymize_local
    hmac_rows=[];mismatches=[]
    for dataset,path,profile in [('A',A,'quality4'),('B',B,'own2')]:
        c=Counter()
        for r in load_rows(path):
            signature=config_hash(dict(version=POLICY_VERSION,model_contract='01313ab-local-pinned',profile=profile,text=fingerprint(r['text'])))
            ev=unpack(json.loads((PRIVATE/'raw'/f'{signature}.json').read_text())['evidence'])
            frozen=frozen_outcome(r['text'],ev)
            with SnapshotDetector(r['text'],subset(ev,'own2')) as detector:
                out=original_prepare(r['id'],r['text'],detector,DIAGNOSTIC_KEY)
            expected='LOCAL_PREPARED' if frozen['ready'] else 'BLOCKED' if frozen['blocked'] else 'REVIEW'
            c['state_mismatches']+=out.state!=expected;c['documents']+=1;c[out.state]+=1
            if out.state!=expected:
                mismatches.append(dict(dataset=dataset,doc_id=r['id'],expected=expected,actual=out.state,reason=out.reason,safe_issue_code=out.safe_issue_code))
            if out.state=='LOCAL_PREPARED' and frozen['ready']:
                c['hmac_outputs_compared']+=1
                c['hmac_mismatches']+=fingerprint(pseudonymize_local(r['text'],frozen['gate'],hmac_key=DIAGNOSTIC_KEY).sanitized_text)!=fingerprint(out.sanitized_text)
                gold={(e['label'],e['start'],e['end']) for e in r['entities']}
                c['emitted_hmac_strict_tp']+=len(frozen['hmac']&gold);c['emitted_hmac_strict_fp']+=len(frozen['hmac']-gold)
        hmac_rows.append(dict(dataset=dataset,**dict(c),scope='actual original prepare+pseudonymizer on real-model cached spans; public diagnostic key, no mappings persisted'))
    write_csv('actual_emitted_hmac.csv',hmac_rows)
    write_json(PRIVATE/'preparation_parity_mismatches.json',mismatches,True)
    if mismatches:raise RuntimeError('FROZEN_PREPARATION_PARITY_FAILED')
    states=defaultdict(Counter);labels=defaultdict(Counter);ablation=[];wins=defaultdict(Counter)
    teacher_default=json.loads((PRIVATE/'teacher'/f'{digest(A)}-default.json').read_text())
    for r in load_rows(A):
        signature=config_hash(dict(version=POLICY_VERSION,model_contract='01313ab-local-pinned',profile='quality4',text=fingerprint(r['text'])))
        ev=unpack(json.loads((PRIVATE/'raw'/f'{signature}.json').read_text())['evidence'])
        gold={(e['label'],e['start'],e['end']) for e in r['entities']}
        baseline_score=raw_score(frozen_outcome(r['text'],ev)['pred'],gold)
        for p in ('F0','F1','F2','F3','F4','F5'):
            o=fusion_outcome(r['text'],ev,p,teacher_default[fingerprint(r['text'])])
            if p=='F1':
                plan=SnapshotGateAdapter(policy='evidence_v2').plan(r['text'],ev)
                state=plan.protection_status
            else:state='CONFIRMED_HMAC_OR_NO_PII' if o['ready'] else 'BLOCKED' if o['blocked'] else 'REVIEW_REQUIRED'
            states[p][state]+=1
            score=raw_score(o['pred'],gold);loss=score['fp']+score['fn'];b_loss=baseline_score['fp']+baseline_score['fn']
            wins[p]['win' if loss<b_loss else 'loss' if loss>b_loss else 'tie']+=1
            for l in sorted(LABELS):labels[(p,l)].update(raw_score({x for x in o['pred'] if x[0]==l},{x for x in gold if x[0]==l}))
    write_csv('fusion_protection_states.csv',[dict(policy=p,**dict(c)) for p,c in states.items()])
    write_csv('fusion_labels_A.csv',[dict(policy=p,label=l,**finish_counts(c)) for (p,l),c in labels.items()])
    write_csv('fusion_document_win_loss.csv',[dict(policy=p,**dict(c)) for p,c in wins.items()])
    ablation_labels=[]
    for option in ('default','no_ensemble','no_boost','drop_unagreed','num_union','name_loose','name_none','with_kiwi'):
        objs=json.loads((PRIVATE/'teacher'/f'{digest(A)}-{option}.json').read_text());counts=Counter()
        per_label=defaultdict(Counter)
        for r in load_rows(A):
            obj=objs[fingerprint(r['text'])];masks=merge_intervals([(s['start'],s['end']) for s in obj['spans']],len(r['text']))
            counts['whole_gold']+=sum(covers((e['start'],e['end']),masks) for e in r['entities'])
            counts['review_candidates']+=len(obj['review'])
            pred={(s['label'],s['start'],s['end']) for s in obj['spans']};gold={(e['label'],e['start'],e['end']) for e in r['entities']}
            for label in sorted(LABELS):per_label[label].update(raw_score({s for s in pred if s[0]==label},{s for s in gold if s[0]==label}))
        ablation.append(dict(option=option,**dict(counts),gold=912))
        ablation_labels.extend(dict(option=option,label=l,**finish_counts(c)) for l,c in per_label.items())
    write_csv('team_protection_ablation.csv',ablation)
    write_csv('team_ablation_labels.csv',ablation_labels)
    print('P2 diagnostics COMPLETE',flush=True)

if __name__=='__main__':main()
