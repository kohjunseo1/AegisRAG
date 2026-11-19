"""Lean sequential A-D experiment. Only four aggregate CSVs and one Markdown report.

All snapshots, locks, manifests and detailed diagnostics live outside Git (0600).
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from . import selection as S
from .contracts import fingerprint,LABELS,ContractError
from .experiments import load_rows,finish_counts,DIAGNOSTIC_KEY
from .lean_policy import VERSION,typed_plan,materialize
from .protection import covers

ROOT=S.ROOT;OUT=ROOT/'reports/unified_v1/lean';PRIVATE=S.PRIVATE.parent/'lean'
FOUR=Path('/tmp/aegis-lean-4team-reference');MODEL_PY='/tmp/aegis-unified-followup-model-env/bin/python'


def save(name,value):S.write_json(PRIVATE/name,value,True)
def read(name):return json.loads((PRIVATE/name).read_text())
def csvout(name,rows):
    OUT.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(rows)
def triples(spans):return {(s['label'],s['start'],s['end']) for s in spans}
def gold(r):return triples(r['entities'])
def spanlist(pred):return [dict(label=l,start=a,end=b) for l,a,b in sorted(pred)]


class Worker:
    def __init__(self,candidate,python=MODEL_PY):self.candidate=candidate;self.python=python;self.process=None
    def __enter__(self):
        env=dict(PATH=os.environ.get('PATH',''),PYTHONPATH=str(ROOT/'step2_privacy_rag/app'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',PYTHONDONTWRITEBYTECODE='1')
        self.process=subprocess.Popen([self.python,str(Path(__file__).with_name('lean_worker.py')),str(S.REFERENCE),self.candidate],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env)
        return self
    def call(self,text,**kw):
        self.process.stdin.write(json.dumps(dict(text=text,**kw))+'\n');self.process.stdin.flush()
        import select
        if not select.select([self.process.stdout],[],[],180)[0]:raise ContractError('LEAN_WORKER_TIMEOUT')
        line=self.process.stdout.readline()
        if not line:raise ContractError('LEAN_WORKER_EXIT')
        obj=json.loads(line)
        if obj.get('error'):raise ContractError(obj['error']+'_'+obj['error_type'])
        return obj
    def __exit__(self,*a):
        self.process.stdin.close()
        try:self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


def verified_inputs():
    old=json.loads((S.OUT/'EXPERIMENT_MANIFEST.json').read_text())
    assert old['datasets']['A']['sha256']==S.digest(S.A)
    assert old['datasets']['B']['sha256']==S.digest(S.B)
    assert old['reference_head']==subprocess.check_output(['git','rev-parse','HEAD'],cwd=S.REFERENCE,text=True).strip()
    from .runtime import KLUE_HASHES,KOELECTRA_HASHES
    assert old['model_hashes']==dict(klue=KLUE_HASHES,koelectra=KOELECTRA_HASHES)
    for key in ['selection_teacher.py','runtime.py','replay.py','worker.py','adapters.py']:
        matches=[v for k,v in old['final_workspace_source_sha256'].items() if k.endswith('/'+key)]
        assert len(matches)==1 and matches[0]==S.digest(Path(__file__).with_name(key)),key
    assert json.loads((ROOT/'step1_pii_detector/src/pii_detector/model_manifest.json').read_text())==old['own_frozen_model_manifest']
    raw={}
    for path in (S.PRIVATE/'raw').glob('*.json'):
        obj=json.loads(path.read_text());ev=obj['evidence']
        if ev['detector_profile']=='quality4':raw[ev['original_input_fingerprint']]=obj
    for r in load_rows(S.A):assert fingerprint(r['text']) in raw
    return old,raw


def phase_a():
    old,raw=verified_inputs();rows=load_rows(S.A);preds={};totals=defaultdict(Counter);taxonomy=[];second=defaultdict(Counter)
    four_rows=load_rows(FOUR/'실전프로젝트1/holdout/holdout_labeled.jsonl')
    assert [(r['id'],r['text'],r['entities']) for r in rows]==[(r['id'],r['text'],r['entities']) for r in four_rows]
    for name,f in [('B2_before','preds/holdout_before/rules+atonlee_holdout.jsonl'),('B2_after','preds/holdout_after/rules+atonlee_holdout.jsonl'),('B2','preds/combined/rules+atonlee_holdout.jsonl')]:
        pr={r['id']:r for r in load_rows(FOUR/'실전프로젝트1'/f)};assert set(pr)=={r['id'] for r in rows}
        preds[name]={r['id']:dict(spans=pr[r['id']]['entities'],raw=pr[r['id']]['entities'],review=[]) for r in rows}
    team=json.loads((S.PRIVATE/'teacher'/f'{S.digest(S.A)}-no_ensemble.json').read_text())
    preds['B1']={r['id']:team[fingerprint(r['text'])] for r in rows}
    for r in rows:
        tr=json.loads((S.PRIVATE/'teacher_raw'/(fingerprint(r['text'])+'.json')).read_text())
        preds['B1'][r['id']]['raw']=tr['klue']+tr['koelectra']
    # Original model_predict snapshots are distinct from adapter's generic KLUE raw.
    for candidate in ('C1','C2'):
        preds[candidate]={}
        variants=['person','address','numbers'] if candidate=='C2' else []
        with Worker(candidate) as w:
            for n,r in enumerate(rows,1):
                text=r['text'];fp=fingerprint(text)
                tr=json.loads((S.PRIVATE/'teacher_raw'/(fp+'.json')).read_text())
                base=tr['klue'] if candidate=='C1' else raw[fp]['evidence']['raw_candidates_by_source']['ax']
                preds[candidate][r['id']]=w.call(text,replay=True,raw=base)
                if candidate=='C2':
                    for rule in variants:
                        name='C2_only_'+rule;preds.setdefault(name,{})[r['id']]=w.call(text,replay=True,raw=base,enabled=[rule])
                        name='C2_off_'+rule;preds.setdefault(name,{})[r['id']]=w.call(text,replay=True,raw=base,enabled=[x for x in variants if x!=rule])
                if n%40==0:print(json.dumps(dict(phase='A',candidate=candidate,documents=n)),flush=True)
    from pii_detector.detector import fuse_labelwise,validate_boundaries
    from pii_detector.structural_policy import refine_spans
    from .replay import SnapshotDetector,SnapshotGateAdapter
    from privacy_gate import check_privacy_gate
    for r in rows:
        text=r['text'];ev=S.unpack(raw[fingerprint(text)]['evidence']);g=gold(r)
        ax=spanlist({(c.label,c.start,c.end) for c in ev.raw_candidates_by_source['ax']});veil=spanlist({(c.label,c.start,c.end) for c in ev.raw_candidates_by_source['veil']})
        stages=[('AX_raw',ax),('Veil_raw',veil)]
        fused,_=fuse_labelwise(ax,veil);bounded,_=validate_boundaries(text,fused);refined,_=refine_spans(text,bounded)
        with SnapshotDetector(text,ev) as det:
            step1=det.detect(text);gate=check_privacy_gate(det,text)
        final=S.frozen_outcome(text,ev)
        stages += [('AX_Veil_raw_union',ax+veil),('fuse_labelwise',fused),('validate_boundaries',bounded),('refine_spans',refined),('Step1_entities',step1['entities']),('Gate_entities',gate.get('entities',[])),('Gate_HMAC',gate.get('entities_for_pseudonymization',[])),('B0',spanlist(final['pred'])),('prepare_HMAC',spanlist(final['hmac']) if final['ready'] else [])]
        preds.setdefault('B0',{})[r['id']]=dict(spans=spanlist(final['pred']),raw=ax+veil,review=[],frozen_masks=final['masks'],frozen_state='LOCAL_PREPARED' if final['ready'] else 'BLOCKED' if final['blocked'] else 'REVIEW_REQUIRED')
        preds.setdefault('B0_detector',{})[r['id']]=dict(spans=step1['entities'],raw=ax+veil,review=[])
        previous=None
        for name,sp in stages:
            p=triples(sp);totals[name].update(S.raw_score(p,g))
            if previous is not None and name not in ('AX_raw','Veil_raw','AX_Veil_raw_union'):
                removed=previous-p;added=p-previous
                causes=Counter()
                for l,a,b in removed:
                    reason='BOUNDARY' if any(k==l and a<d and b>c for k,c,d in added) else 'LABEL' if any(a==c and b==d for k,c,d in added) else 'HMAC_NOT_CONFIRMED' if 'HMAC' in name else 'REVIEW' if name=='Gate_entities' and gate.get('decision')!='READY_FOR_LOCAL_PSEUDONYMIZATION' else 'DROP'
                    causes[reason]+=1
                for reason,count in causes.items():taxonomy.append(dict(section='v6_transition',doc_id=r['id'],stage=name,reason=reason,count=count,recovered_tp=len((p-previous)&g),lost_tp=len((previous-p)&g),removed_fp=len((previous-p)-g),created_fp=len((p-previous)-g)))
            previous=p
        base=triples(ax)
        for s in ('veil','koelectra'):
            other={(c.label,c.start,c.end) for c in ev.raw_candidates_by_source[s] if c.label in LABELS}
            for label in sorted(LABELS):
                new={x for x in other-base if x[0]==label};v=second[s,label];v['additional_exact_tp']+=len(new&g);v['additional_fp']+=len(new-g)
                v['boundary_alternatives']+=sum(any(l==k and a<d and b>c and (a,b)!=(c,d) for k,c,d in base) for l,a,b in new)
                v['oracle_recoverable_boundary_tp']+=sum(x in g and any(l==x[0] and a<x[2] and b>x[1] for l,a,b in base) for x in new)
    for name,pr in preds.items():
        if name=='B0':continue
        for r in rows:totals[name].update(S.raw_score(triples(pr[r['id']]['spans']),gold(r)))
    # Every rule's paired effect is scored; policy itself has no Gold input.
    for name in [n for n in preds if n.startswith('C2_')]:
        for r in rows:
            base=triples(preds['C2'][r['id']]['raw']) if '_only_' in name else triples(preds['C2'][r['id']]['spans'])
            p=triples(preds[name][r['id']]['spans']);g=gold(r)
            taxonomy.append(dict(section='rule_ablation',doc_id=r['id'],stage=name,reason='PAIRED_DELTA',count=1,recovered_tp=len((p-base)&g),lost_tp=len((base-p)&g),removed_fp=len((base-p)-g),created_fp=len((p-base)-g)))
    score_rows=[dict(dataset='A',candidate=n,label='ALL',comparison='SAME_INPUT_GOLD_SCORER',**finish_counts(v),overlap_gold=v['overlap_gold'],fully_missed=v['fully_missed']) for n,v in totals.items()]
    for name,pr in preds.items():
        for label in sorted(LABELS):
            ct=Counter()
            for r in rows:ct.update(S.raw_score({x for x in triples(pr[r['id']]['spans']) if x[0]==label},{x for x in gold(r) if x[0]==label}))
            score_rows.append(dict(dataset='A',candidate=name,label=label,comparison='SAME_INPUT_GOLD_SCORER',**finish_counts(ct),overlap_gold=ct['overlap_gold'],fully_missed=ct['fully_missed']))
    csvout('A_same_scorer.csv',score_rows)
    csvout('A_error_taxonomy.csv',taxonomy)
    csvout('A_second_model_value.csv',[dict(source=s,label=l,**dict(v),scope='oracle_diagnostic_not_runtime_selection') for (s,l),v in second.items()])
    save('A_predictions.json',preds)
    decision=dict(phase='A_COMPLETE',single_first=['C1','C2'],B2_comparable=True,B2_tuned_on_holdout=True,
        C3='DEFERRED_UNTIL_COMMON_ADAPTER_SINGLE_MODEL_RESULTS',C4='NOT_PROMOTED_no_additional_exact_TP_advantage_over_Veil',
        totals={n:finish_counts(v) for n,v in totals.items()})
    save('A_decision.json',decision)
    save('manifest.json',dict(version=VERSION,head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),reference_head=old['reference_head'],four_team_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=FOUR,text=True).strip(),A_sha=S.digest(S.A),B_sha=S.digest(S.B),scorer_sha=S.digest(S.__file__),model_hashes=old['model_hashes'],own_models=old['own_frozen_model_manifest'],raw_snapshot_contract_verified=True,paired_bootstrap_seed=20261119,equivalence_f1_margin=.005))
    print(json.dumps(decision),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['A','B','C','D','report']);p.add_argument('--runtime-python',default=MODEL_PY);a=p.parse_args()
    {'A':phase_a,'B':phase_b,'C':lambda:phase_c(a.runtime_python),'D':lambda:phase_d(a.runtime_python),'report':report}[a.phase]()

# Later phases deliberately require persisted prior decisions.

def bootstrap_pairs(rows,left,right,samples=2000):
    rng=random.Random(20261119);scores=[];wins=ties=losses=0
    per=[]
    for r in rows:
        a=S.raw_score(triples(left[r['id']]['spans']),gold(r));b=S.raw_score(triples(right[r['id']]['spans']),gold(r));per.append((a,b))
        delta=finish_counts(a)['f1']-finish_counts(b)['f1'];wins+=delta>0;ties+=delta==0;losses+=delta<0
    for _ in range(samples):
        a=Counter();b=Counter()
        for i in (rng.randrange(len(per)) for _ in per):a.update(per[i][0]);b.update(per[i][1])
        scores.append(finish_counts(a)['f1']-finish_counts(b)['f1'])
    scores.sort();return dict(paired_win=wins,paired_tie=ties,paired_loss=losses,bootstrap_delta_low=scores[int(samples*.025)],bootstrap_delta_high=scores[int(samples*.975)],bootstrap_samples=samples)


def score_prepared(rows,pred):
    c=Counter();errors=Counter();details=[];consistent_tokens={}
    for r in rows:
        text=r['text'];o=pred[r['id']];g=gold(r);c.update(S.raw_score(triples(o['spans']),g))
        plan=typed_plan(text,o['spans'],o.get('raw',o['spans']),review=o.get('review',[]))
        c[plan['state']]+=1;c['gold']+=len(g);c['plan_protected']+=sum(covers((a,b),plan['masks']) for _,a,b in g)
        c['pending']+=len(plan['pending']);c['hmac_proposed_tp']+=len(triples(plan['hmac'])&g);c['hmac_proposed_fp']+=len(triples(plan['hmac'])-g)
        c['floor_lost_characters']+=sum(not covers((i,i+1),plan['masks']) for a,b in o.get('frozen_masks',[]) for i in range(a,b))
        try:
            preview=materialize(text,plan,DIAGNOSTIC_KEY,preview=True)
            c['preview_materialized']+=1
            if plan['state']=='LOCAL_PREPARED':
                actual=materialize(text,plan,DIAGNOSTIC_KEY)
                c['actual_ready']+=1;c['ready_gold']+=len(g)
                # Replacements cover original codepoints, even when token length differs.
                c['actual_ready_protected']+=sum(covers((a,b),plan['masks']) for _,a,b in g)
                c['actual_hmac_tp']+=len(triples(plan['hmac'])&g);c['actual_hmac_fp']+=len(triples(plan['hmac'])-g)
                for entity in actual.safe_token_manifest:
                    l,a,b=entity['label'],entity['start'],entity['end']
                    if (l,a,b) not in g:
                        reason='wrong_type_exact_boundary' if any(a==x and b==y and l!=k for k,x,y in g) else 'wrong_boundary_same_type_overlap' if any(l==k and a<y and b>x for k,x,y in g) else 'unlabeled_or_unmatched'
                        c['actual_hmac_'+reason]+=1
                    from local_pseudonymizer import make_hmac_token,canonicalize
                    tok,_=make_hmac_token(DIAGNOSTIC_KEY,l,text[a:b])
                    signature=(l,canonicalize(l,text[a:b]))
                    c['hmac_token_consistency_errors']+=entity['token']!=tok or (signature in consistent_tokens and consistent_tokens[signature]!=tok)
                    consistent_tokens[signature]=tok

        except ValueError as exc:errors[type(exc).__name__]+=1;c['materialization_blocked']+=1
        details.append(dict(doc_id=r['id'],state=plan['state'],hmac_fp=len(triples(plan['hmac'])-g),missing=sum(not covers((a,b),plan['masks']) for _,a,b in g)))
    return dict(**finish_counts(c),**{k:v for k,v in c.items() if k not in ('tp','fp','fn')},materialization_errors=dict(errors)),details


def phase_b():
    decision=read('A_decision.json');assert decision['phase']=='A_COMPLETE'
    rows=load_rows(S.A);preds=read('A_predictions.json');result={};details={}
    # Drop neutral person correction from the proposed C2 runtime: zero A delta.
    preds['C2']=preds['C2_off_person']
    for name in ('B0','B1','B2','C1','C2'):
        # F0 protection floor is an offline reference only, never a runtime head.
        for r in rows:preds[name][r['id']]['frozen_masks']=preds['B0'][r['id']]['frozen_masks']
        scored=preds[name]
        if name=='B0':
            # Common adapter receives original Step1 detector typed spans, not
            # already gated 843 entities. Native gate is a separate table.
            import copy
            from pii_detector.detector import fuse_labelwise,validate_boundaries
            from pii_detector.structural_policy import refine_spans
            scored=copy.deepcopy(scored)
            _,snaps=verified_inputs()
            for r in rows:
                ev=S.unpack(snaps[fingerprint(r['text'])]['evidence'])
                heads={src:spanlist({(c.label,c.start,c.end) for c in ev.raw_candidates_by_source[src]}) for src in ('ax','veil')}
                fs,_=fuse_labelwise(heads['ax'],heads['veil']);bs,_=validate_boundaries(r['text'],fs);es,_=refine_spans(r['text'],bs)
                scored[r['id']]['spans']=es
        result[name],details[name]=score_prepared(rows,scored)
        result[name].update(bootstrap_pairs(rows,scored,preds['B2']))
        result[name]['model_count']=2 if name in ('B0','B1') else 1
    best=max(('C1','C2'),key=lambda n:result[n]['f1'])
    # C1 already matches B2 in F1 at one checkpoint. A second model has no
    # demonstrated safety benefit until single-model protection review is solved.
    lock=dict(phase='B_COMPLETE',shortlist=['C1','C2'],detection_leader=best,C2_enabled=['address','numbers'],
        C3='NOT_PROMOTED_single_model_C1_within_0.005_of_B2_no_proven_safety_gain',
        C4='NOT_PROMOTED_no_greater_exact_candidate_gain_than_Veil',
        winner_pending='REAL_LATENCY_AND_SAFETY_VALIDATION',results=result)
    save('B_predictions.json',preds);save('B_decision.json',lock);save('B_private_diagnostics.json',details)
    print(json.dumps(lock),flush=True)


def real_run(candidate,rows,python, *, enabled=None):
    values={};metrics=[]
    with Worker(candidate,python) as w:
        warm=w.call('성명: 금재원\n전화: 010-0000-0000\n합성 교육 안내',**({'enabled':enabled} if enabled else {}))
        for number,r in enumerate(rows,1):
            obj=w.call(r['text'],**({'enabled':enabled} if enabled else {}));assert obj['mode']=='REAL_INFERENCE'
            values[r['id']]=obj;metrics.append({k:obj[k] for k in ('inference_ms','cold_load_ms','forwards','peak_rss_mb')})
            if number%25==0:print(json.dumps(dict(phase='C_REAL',candidate=candidate,documents=number)),flush=True)
    return values,dict(**S.summary_ms([m['inference_ms'] for m in metrics]),peak_rss_mb=max(m['peak_rss_mb'] for m in metrics),cold_load_ms=warm['cold_load_ms'],
        forwards=dict(sum((Counter(m['forwards']) for m in metrics),Counter())),threads=2,cold_samples=1,warm_documents=len(rows))


def phase_c(python):
    lock=read('B_decision.json');assert lock['phase']=='B_COMPLETE';rows=load_rows(S.A);replay=read('B_predictions.json');live={};latency=read('C_latency.json') if (PRIVATE/'C_latency.json').exists() else {};parity=read('C_parity.json') if (PRIVATE/'C_parity.json').exists() else {}
    # A resumed timing needs exact input/code/reference/model-contract hashes.
    contracts=read('C_resume_contracts.json') if (PRIVATE/'C_resume_contracts.json').exists() else {}
    current_contract=dict(A_sha=S.digest(S.A),worker_sha=S.digest(Path(__file__).with_name('lean_worker.py')),policy_sha=S.digest(Path(__file__).with_name('lean_policy.py')),reference_sha=read('manifest.json')['reference_head'],models=read('manifest.json')['model_hashes'],own_models=read('manifest.json')['own_models'])
    # Same machine, same A order, 2 torch/OMP/MKL threads, no inference cache.
    for name in ('C1','C2','B1','B0'):
        if contracts.get(name)==current_contract and name in latency and (PRIVATE/('C_live_A_'+name+'.json')).exists():
            pr=read('C_live_A_'+name+'.json');t=latency[name]['A']
            print(json.dumps(dict(phase='C_RESUME_MEASURED',candidate=name,documents=len(pr))),flush=True)
        else:pr,t=real_run(name,rows,python,enabled=lock['C2_enabled'] if name=='C2' else None)
        live[name]=pr;latency[name]=dict(A=t)
        parity[name]=sum(triples(pr[r['id']]['spans'])!=triples(replay[name][r['id']]['spans']) for r in rows)
        save('C_live_A_'+name+'.json',pr);save('C_latency.json',latency);save('C_parity.json',parity)
        contracts[name]=current_contract;save('C_resume_contracts.json',contracts)
        if parity[name]:raise ContractError('LEAN_REAL_REPLAY_PARITY_FAILED_'+name)
    # Freeze single candidate before exposed B; no B-based rule or threshold edits.
    aresults=lock['results'];eligible=[n for n in ('C1','C2') if aresults[n].get('actual_ready',0)>0 and aresults[n].get('actual_ready_protected',0)==aresults[n].get('ready_gold',0) and aresults[n].get('actual_hmac_fp',0)<=7 and aresults[n].get('floor_lost_characters',0)==0]
    # A plan failing the protection floor is not silently promoted for throughput.
    winner=max(eligible,key=lambda n:(aresults[n]['f1'],-latency[n]['A']['p95_ms'])) if eligible else 'B0'
    chosen_for_B=lock['detection_leader'];save('C_selection_lock.json',dict(selected=winner,distribution_candidate=chosen_for_B,A_only=True,rule_hash=S.digest(Path(__file__).with_name('lean_policy.py')),reason='common_adapter_safety_floor_first_then_f1_and_latency'))
    b=load_rows(S.B);bpred,t=real_run(chosen_for_B,b,python,enabled=lock['C2_enabled'] if chosen_for_B=='C2' else None)
    save('C_live_B_'+chosen_for_B+'.json',bpred);latency[chosen_for_B]['B']=t
    bscore,bdetails=score_prepared(b,bpred);save('C_B_diagnostics.json',bdetails)
    # Baseline long-input latency is measured on the same B order, not scored for retuning.
    b0pred,b0time=real_run('B0',b,python);save('C_live_B_B0.json',b0pred);latency['B0']['B']=b0time
    # A frozen finalist is evaluated once, without using B to pick another.
    save('C_latency.json',latency);save('C_decision.json',dict(phase='C_COMPLETE',selected=winner,detection_leader=chosen_for_B,latency=latency,parity=parity,B=bscore,B_status='PREVIOUSLY_EXPOSED_DISTRIBUTION_SHIFT_NOT_FRESH_BLIND',production_transition=False))
    finish_native_measurements()
    write_pareto()


def finish_native_measurements():
    from .replay import SnapshotDetector
    summaries={}
    for dataset,path in [('A',S.A),('B',S.B)]:
        live=read('C_live_'+dataset+'_B0.json');ct=Counter();native=Counter()
        for r in load_rows(path):
            ev=evidence_from_live(r['text'],live[r['id']]);g=gold(r)
            with SnapshotDetector(r['text'],ev) as det:original=det.detect(r['text'])
            ct.update(S.raw_score(triples(original['entities']),g))
            f=S.frozen_outcome(r['text'],ev)
            native.update(S.score_fusion(dict(f,pending=0),g))
            if f['ready']:
                native['actual_ready_gold']+=len(g);native['actual_ready_protected']+=sum(covers((a,b),f['masks']) for _,a,b in g)
                native['actual_ready_hmac_tp']+=len(f['hmac']&g);native['actual_ready_hmac_fp']+=len(f['hmac']-g)
        summaries[dataset]=dict(detector=finish_counts(ct),native=dict(**finish_counts(native),**{k:v for k,v in native.items() if k not in ('tp','fp','fn')}))
    save('C_native.json',summaries)


def write_pareto():
    b=read('B_decision.json');c=read('C_decision.json');rows=[]
    for name,v in b['results'].items():
        row=dict(dataset='A',candidate=name,models={'B0':'ax+veil','B1':'klue+koelectra','B2':'atonlee','C1':'klue','C2':'ax'}[name],approval_contract='COMMON_ADAPTER',**{k:val for k,val in v.items() if not isinstance(val,dict)})
        row.update(c['latency'].get(name,{}).get('A',{}));row.pop('forwards',None)
        row['selection']='NATIVE_B0_SELECTED_COMMON_ADAPTER_REJECTED' if name==c['selected'] else 'NOT_SELECTED'
        row['ood_f1_delta']=c['B']['f1']-v['f1'] if name==c['detection_leader'] else ''
        row['latency_status']='REAL_MEASURED' if name in c['latency'] else 'BLOCKED_NO_SAME_CONDITION_RUNTIME' if name=='B2' else 'PRIOR_MEASUREMENT_ONLY'
        rows.append(row)
    rows.append(dict(dataset='B',candidate=c['detection_leader'],**{k:v for k,v in c['B'].items() if not isinstance(v,dict)},**{k:v for k,v in c['latency'][c['detection_leader']]['B'].items() if k!='forwards'}))
    if (PRIVATE/'C_native.json').exists():
        for ds,v in read('C_native.json').items():
            rows.append(dict(dataset=ds,candidate='B0_NATIVE',models='ax+veil',model_count=2,approval_contract='ORIGINAL_FROZEN_V6',**v['native']))
            rows.append(dict(dataset=ds,candidate='B0_DETECTOR',models='ax+veil',model_count=2,approval_contract='DETECTOR_ONLY',**v['detector']))
    csvout('C_pareto.csv',rows)

def evidence_from_live(text,obj,kind='document'):
    from .contracts import Candidate,DetectionEvidence,config_hash
    by=obj.get('by_source')
    if not by:raise ContractError('LEAN_MISSING_SOURCE_SEPARATION')
    return DetectionEvidence(kind,'lean', {s:tuple(Candidate(s,'pinned-frozen-v6',r['label'],r['start'],r['end'],fingerprint(text),evidence_codes=tuple(r.get('evidence_codes',()))) for r in rs) for s,rs in by.items()},
        {s:'OK' for s in by},(),config_hash({'lean':VERSION,'profile':'own2'}),fingerprint(text),'own2')


def phase_d(python):
    c=read('C_decision.json');assert c['phase']=='C_COMPLETE'
    # Current measured decision is the unchanged native F0 safety contract.
    # Common-adapter candidates do not silently bypass failed safety criteria.
    if c['selected']!='B0':raise ContractError('LEAN_UNVALIDATED_FINALIST_ADAPTER')
    from document_preparation import prepare as frozen_prepare
    from dataset_preparation import fixed_key,key_tag
    from .selection_retrieval import ENV,ARTIFACT,score_ranking
    from .replay import SnapshotDetector
    from .contracts import PreparationResult,Candidate,ProtectionPlan
    from .retrieval import InMemoryIndex,IndexedDocument
    from .generation import PrivacyRecheck,ContextBuilder,MockTransport,MockGenerator
    from .embeddings import KoE5Encoder
    from .identity import extract_owned_fields
    from .pipeline import query_from_mentions
    from .sidecar import legacy_lookup_key
    from .experiments import local_approve
    from region_keys import keys_from_query
    key=fixed_key(ENV);tag=key_tag(key);policy=VERSION+'_frozen_v6';corpus='lean_isolated'
    counts=Counter();forwards=Counter();guardcache={};guardcost=[];document_states=Counter();failures=Counter();querydetails=[];privatedetails=[]
    import torch
    torch.set_num_threads(2)
    encoder=KoE5Encoder()
    with Worker('B0',python) as w:
        class Independent:
            def detect(self,text, *, input_kind):
                fp=fingerprint(text);counts['guard_requests']+=1
                if fp in guardcache:counts['guard_snapshot_hits']+=1;return guardcache[fp]
                obj=w.call(text);forwards.update(obj['forwards']);counts['guard_real_requests']+=1;guardcost.append(obj['inference_ms'])
                ev=evidence_from_live(text,obj,input_kind);guardcache[fp]=ev;return ev
        guard=PrivacyRecheck(Independent())
        def prepare_text(did,text,kind,obj=None):
            if obj is None:
                obj=w.call(text);forwards.update(obj['forwards']);counts['prepare_real_requests']+=1
            else:counts['prepare_A_real_snapshot_reuse']+=1
            ev=evidence_from_live(text,obj,kind)
            with SnapshotDetector(text,ev) as det:out=frozen_prepare(('Q_' if kind=='query' else 'DOC_')+did,text,det,key)
            if out.state!='LOCAL_PREPARED':raise ContractError('FROZEN_'+out.state)
            prep=PreparationResult('LOCAL_PREPARED','LINKED','NOT_APPROVED',out.sanitized_text,tag,policy,kind,coverage_verified=True)
            f=S.frozen_outcome(text,ev)
            cs=tuple(Candidate('veil' if l=='address' else 'ax','frozen-v6',l,a,b,fingerprint(text)) for l,a,b in sorted(f['hmac']))
            plan=ProtectionPlan(cs,cs,tuple(tuple(x) for x in f['masks']),(),(),(),'own2',VERSION,fingerprint(text),kind)
            identity=extract_owned_fields(text,plan,doc_id=did,hmac_key=key,key_tag=tag)
            return prep,identity
        def newindex(name):return InMemoryIndex(key_tag=tag,policy_version=policy,corpus_id=corpus,index_version=name)
        def insert(ix,vectors,did,text,obj=None):
            prep,identity=prepare_text(did,text,'document',obj)
            p,a=local_approve(prep,guard,corpus)
            # No invented ownership or cross-document identity. Existing conservative
            # lookup-only candidate behavior still requires clarification.
            lookups=tuple(sorted({m.name_lookup_key for m in identity.mentions if m.name_lookup_key}))
            ix.add(IndexedDocument(did,p,a,tuple(keys_from_query(text,key)),(),None,lookups))
            vectors[did]=encoder.encode_passage(p)
        def runquery(ix,vectors,qid,text):
            stage='gate';ranks=[];error='';transport=None
            builder=ContextBuilder(guard,key_tag=tag,policy_version=policy,index_version=ix.index_version,corpus_id=corpus)
            started=time.perf_counter()
            try:
                prep,identity=prepare_text(qid,text,'query')
                stage='query_recheck';p,a=local_approve(prep,guard,corpus)
                spec=query_from_mentions(p,a,identity.mentions,regions=tuple(keys_from_query(text,key)))
                stage='retrieval';qv=encoder.encode_query(p)
                evidence=ix.retrieve(spec,profile='own_v6',top_k=5,dense_scorer=lambda q,docs:{d.doc_id:sum(x*y for x,y in zip(vectors[d.doc_id],qv)) for d in docs})
                ranks=[e.doc_id for e in evidence]
                if not evidence:raise ContractError('NO_EVIDENCE')
                stage='context_recheck'
                transport=MockTransport(dict(decision='ANSWER',answer='검색된 로컬 문서를 확인했습니다.',citations=[evidence[0].doc_id]))
                MockGenerator(builder,transport).generate(spec,evidence)
                stage='complete'
            except ContractError as exc:
                error=str(exc)
                if stage=='context_recheck':stage=getattr(builder,'last_stage',stage)
            finally:
                if transport is not None:counts['mock_calls']+=transport.calls
            return dict(mock_transport_calls=transport.calls if transport is not None else 0,qid=qid,first_failure_stage=stage,error=error,actual_top5=ranks,latency_ms=(time.perf_counter()-started)*1000)
        smoke=newindex('lean_smoke');sv={}
        synth=[('education','고객명: 금재원\n전화: 010-0000-0000\n교육 신청은 승인 후 완료됩니다.'),('travel','출장비 신청서는 재무 부서에 제출합니다.'),('security','보안 교육은 금요일에 진행됩니다.')]
        for did,text in synth:insert(smoke,sv,did,text)
        smokequery=runquery(smoke,sv,'synthetic_query','교육 신청 절차를 알려주세요.')
        if smokequery['first_failure_stage']!='complete':raise ContractError('LEAN_SYNTHETIC_E2E_FAILED')
        negative=0
        for text,allowed in [('전화 010-7654-3210',None),('<PERSON_'+'a'*24+'>',set()),('<PERSON_1234>',None)]:
            try:guard.check(text,allowed_tokens=allowed)
            except ContractError:negative+=1
        if negative!=3:raise ContractError('LEAN_NEGATIVE_GUARD_FAILED')
        save('D_smoke.json',dict(status='PASS',indexed=len(smoke.ids),normal_mock=1,privacy_negative_blocked=negative,paid_calls=0,sql_writes=0))
        # Only after smoke, one pass over all 88 real queries. No operating corpus.
        ix=newindex('lean_selected_187_prepared');vectors={};live=read('C_live_A_B0.json')
        for n,r in enumerate(load_rows(S.A),1):
            try:insert(ix,vectors,r['id'],r['text'],live[r['id']]);document_states['INDEXED']+=1
            except ContractError as exc:document_states['WITHHELD']+=1;failures[str(exc)]+=1
            if n%25==0:print(json.dumps(dict(phase='D_INDEX',documents=n,states=dict(document_states))),flush=True)
        cases=load_rows(S.DATA/'holdout_retrieval_queries.jsonl');assert len(cases)==88
        goals={g['qid']:g for g in json.loads(ARTIFACT.read_text())['gold_for_evaluation_only']};ranking=Counter()
        for n,r in enumerate(cases,1):
            detail=runquery(ix,vectors,r['qid'],r['query']);querydetails.append(detail)
            metric=score_ranking(detail['actual_top5'],goals[r['qid']],bool(detail['actual_top5']))
            ranking.update({k:metric[k] for k in ['hit1','hit5','online_hit5']})
            privatedetails.append(dict(qid=r['qid'],stage=detail['first_failure_stage'],diagnostics=guard.last_diagnostics))
            if n%10==0:print(json.dumps(dict(phase='D_E2E',queries=n,stages=dict(Counter(d['first_failure_stage'] for d in querydetails)))),flush=True)
    save('D_queries.json',querydetails);save('D_private_diagnostics.json',privatedetails)
    result=dict(phase='D_COMPLETE',selected='B0',smoke='PASS',documents=dict(document_states),document_failures=dict(failures),queries=88,
        stages=dict(Counter(d['first_failure_stage'] for d in querydetails)),errors=dict(Counter(d['error'] for d in querydetails if d['error'])),
        actual_hit5_numerator=ranking['hit5'],actual_hit5_denominator=88,returned_limit=5,guard_model_checkpoints=['ax','veil'],
        model_forwards=dict(forwards),counters=dict(counts),guard_real_cost=S.summary_ms(guardcost),
        e2e_latency=S.summary_ms([d['latency_ms'] for d in querydetails]),index='ISOLATED_MEMORY_SAME_POLICY_DOCUMENTS_AND_QUERIES',
        paid_calls=0,sql_writes=0,external_private_transfers=0,factuality='NOT_MEASURED_MOCK_ONLY',
        identity='CONSERVATIVE_LOOKUP_NO_INVENTED_ENTITY_OWNERSHIP',performance_scope='guard uses selected two checkpoint raw re-inference; within-run exact sanitized snapshot reuse counted')
    save('D_decision.json',result);print(json.dumps(result),flush=True)

def report():
    m=read('manifest.json');a=read('A_decision.json');b=read('B_decision.json')
    c=read('C_decision.json') if (PRIVATE/'C_decision.json').exists() else None
    d=read('D_decision.json') if (PRIVATE/'D_decision.json').exists() else None
    lines=['# Lean 최종 구성 결정 — 2026-11-19','',
        '**최종 안전 기준선: B0, 기존 A.X+Veil + Frozen V6/96-bit HMAC + KoE5 + own_v6. 운영 전환 없음. 실험 종료 후 사용자 요청으로 작업 브랜치에만 커밋·push한다.**',
        'C1(KLUE+Boost 한 모델)은 탐지 성능과 비용 측면에서 가장 유망하다. 그러나 이번 공통 typed adapter는 보호 하한·승인 처리율 동등성을 달성하지 못했다. 탐지부 추천 C1과 안전한 E2E 실행 기준선 B0를 구분한다. 새 어댑터를 기본 런타임에 적용하지 않는다.','',
        '## 수행 범위와 판정','',
        '| 단계 | 결과 |','| --- | --- |',
        '| A | PASS: 동일 scorer, 원본 단계별 attribution, C1/C2 ablation, 두 번째 모델 진단 상한 |',
        '| B | 실험 PASS / 신규 승인 어댑터 채택 FAIL: Mask-Only와 근거 부족 HMAC은 보류 |',
        '| C | '+('실추론·A 재생 parity·고정 B 평가 PASS; 신규 end-to-end 개선 미입증' if c else '진행 중')+' |',
        '| D | '+('합성 smoke·차단 반례·88질의 단일 로컬 검증 실행 완료; 실제 답변 품질 미측정' if d else '진행 전')+' |','',
        '## 동일 조건의 탐지 성능','',
        'A는 187문서/912 Gold, Unicode [start,end), 동일 7종 strict micro scorer다. 4팀 파일의 ID·원문·Gold 전부 일치한다. B0 탐지부와 Gate 이후 typed 출력을 같은 단계로 취급하지 않는다.','',
        '| 구성 | NER | TP/FP/FN | Strict F1 | 근거/주의 |','| --- | ---: | --- | ---: | --- |']
    for n,label,models,note in [('Step1_entities','B0 탐지부',2,'fuse→boundary→refine, Gate 전'),('B0','B0 Gate typed',2,'READY/보류 정책 반영, Raw 점수와 직접 비교 금지'),('B1','B1 no_ensemble',2,'KLUE+KoELECTRA, 글자 확률 앙상블만 없음'),('B2_before','B2 holdout 수정 전',1,'고정된 과거 예측 파일'),('B2_after','B2 holdout 수정 후',1,'holdout 오류를 보고 보정한 노출된 결과'),('C1','C1 KLUE+Boost',1,'KoELECTRA/확률 앙상블 완전 제거'),('C2','C2 AX+주소/번호',1,'개선 없는 이름 보정 제외')]:
        v=a['totals'][n];lines.append(f"| {label} | {models} | {v['tp']}/{v['fp']}/{v['fn']} | {v['f1']:.6f} | {note} |")
    lines+=['','C1은 B1 대비 exact TP -1, FP -8이며 F1은 0.991266→0.995063이다. KoELECTRA를 켜면 PERSON 신규 exact TP1/FP8, ADDRESS 신규 TP/FP0/0, 원본 검수 후보는 C1 0→B1 17이다. `no_ensemble`을 1모델이라고 부르던 해석을 수정하고, KoELECTRA 미호출을 별도 회귀 테스트했다. C1과 4팀 수정 후 F1 차이는 약 -0.000022다. 이것은 우월성 증명이 아니다.','',
        '## Phase A: 실제 손실·규칙 기여','',
        '| B0 단계 | TP/FP/FN | F1 |','| --- | --- | ---: |']
    for n in ['AX_raw','Veil_raw','fuse_labelwise','validate_boundaries','refine_spans','Step1_entities','Gate_entities','Gate_HMAC','prepare_HMAC']:
        v=a['totals'][n];lines.append(f"| {n} | {v['tp']}/{v['fp']}/{v['fn']} | {v['f1']:.6f} |")
    lines+=['','Step1의 TP 910→Gate 843은 strict typed 출력의 변화다. 실제 코드의 REVIEW/경계/라벨/HMAC 제외를 `A_error_taxonomy.csv`로 추적했다. FN69를 69개 실제 누출로 해석하지 않는다. 원본 V6 제안 Gold 완전 보호 912/912와 실제 READY177의 Gold843/843은 기존 검증을 재사용한다.','',
        '| AX 규칙 단독 | TP/FP/FN | F1 |','| --- | --- | ---: |']
    for n in ['AX_raw','C2_only_person','C2_only_address','C2_only_numbers','C2','C2_off_person','C2_off_address','C2_off_numbers']:
        v=a['totals'][n];lines.append(f"| {n} | {v['tp']}/{v['fp']}/{v['fn']} | {v['f1']:.6f} |")
    lines+=['','주소 보정: raw AX 대비 TP +8, FP -13. 번호 형식/문맥: TP 변화 0, FP -9. 이름 보정: 이 A에서 변화 0으로 기본 C2에서 제외. 각 단독/하나씩 off의 recovered/lost TP·removed/created FP는 집계 CSV에서 문서별로 재현한다. Gold·인물 목록은 정책 함수 인자로 받지 않는다.','',
        'C2가 남긴 FN 2개는 PERSON 경계 문제이고 주소 Gold 52개는 모두 exact 복구했다. Veil과 KoELECTRA 모두 raw AX 대비 새 주소 exact TP3이나, C2 보정 이후 추가 주소 이득은 입증되지 않았다. Veil 신규 주소 FP2 vs KoELECTRA6, PERSON 추가 exact TP0/0이다. 이 값은 정답을 본 진단 상한이며 runtime resolver 점수로 쓰지 않는다. 따라서 C3/C4·cascade·15조합 재실험·새 모델 다운로드는 진행하지 않았다. 두 번째 모델은 현재 공통 어댑터의 이름 승인/Mask-Only 병목을 해결한다는 근거가 없다.','',
        '## Phase B: 공통 typed-plan/HMAC 실제 로컬 준비','',
        '모든 원본 탐지 출력을 같은 어댑터로 평가했다. B0도 Gate 이전 Step1 typed 후보를 입력한다. 원본 승인 정책과 공통 어댑터 결과는 별개다. B1도 원본 KLUE+KoELECTRA raw를 mask 입력에 포함했다(standalone Hybrid의 최종 span 보호910/912와 구분). B2는 승인된 최종 예측 파일만 있어 raw의 추가 마스크 근거는 미확보다. B1 raw 누락을 최종 집계에서 보완한 계측 수정은 C1/C2 정책·선택을 바꾸지 않는다. 원본 96-bit HMAC/canonicalizer/token namespace를 호출하며 원본 키·V6 파일은 변경하지 않았다.','',
        '| 공통 어댑터 입력 | 제안 Gold 보호 | LOCAL_PREPARED/REVIEW/BLOCKED | 실제 READY Gold 보호 | 실제 HMAC TP/FP | F0 마스크 대비 미보호 코드포인트 |','| --- | --- | --- | --- | --- | ---: |']
    for n,v in b['results'].items():
        lines.append(f"| {n} | {v.get('plan_protected',0)}/912 | {v.get('LOCAL_PREPARED',0)}/{v.get('REVIEW_REQUIRED',0)}/{v.get('materialization_blocked',0)} | {v.get('actual_ready_protected',0)}/{v.get('ready_gold',0)} | {v.get('actual_hmac_tp',0)}/{v.get('actual_hmac_fp',0)} | {v.get('floor_lost_characters',0)} |")
    lines+=['','실제 READY의 Gold 분모가 작다. 0/0도 성공적인 이름 가명처리로 해석하지 않는다. 보호 하한의 코드포인트 차이는 알려진 Gold 외 과보호도 포함하므로 실제 누출 개수와 다르지만, 하한 동등성을 주장할 근거도 되지 못한다. REVIEW preview는 원본 pseudonymizer로 로컬 생성해 형식 검증했으며 LOCAL_PREPARED·검색 적재·송신 성공으로 세지 않았다. 확정되지 않은 후보는 마스크로만 보존하고 보류한다.','',
        '원본 B0 native: READY177 / REVIEW10 / BLOCKED0; 실제 READY HMAC TP816/FP7, 원본 prepare parity177/177. 이것은 기존 실측의 재사용이다. FP는 Gold strict 타입/경계 오차이며 암호 함수 불일치와 다르다. 이번 공통 adapter는 B0조차 READY를 크게 줄였으므로 교체 채택 FAIL이다. C1 탐지기가 본질적으로 가명처리에 부적합하다고 단정하지 않는다.','',
        '## 통계·실추론 비용','',
        '문서 단위 bootstrap 2,000회(seed20261119), 아래는 각 후보 − B2의 strict micro F1 차이 95% percentile CI다. A는 노출된 개발셋이며 CI는 분포 이동·미라벨 PII 안전성 보증이 아니다.','',
        '| 후보 | paired win/tie/loss | F1 delta CI |','| --- | --- | --- |']
    for n,v in b['results'].items():lines.append(f"| {n} | {v['paired_win']}/{v['paired_tie']}/{v['paired_loss']} | [{v['bootstrap_delta_low']:.6f}, {v['bootstrap_delta_high']:.6f}] |")
    if (PRIVATE/'stage_cost.json').exists():
        costs=read('stage_cost.json')
        lines+=['','추론과 별도 측정한 공통 Resolver/HMAC preview 비용(187문서, 실제 forward 없음):','']
        for name in ('C1','C2'):
            t=costs[name];lines.append(f"- {name}: Resolver P50/P95 {t['common_adapter_ms']['p50_ms']:.3f}/{t['common_adapter_ms']['p95_ms']:.3f}ms, HMAC preview {t['original_hmac_preview_ms']['p50_ms']:.3f}/{t['original_hmac_preview_ms']['p95_ms']:.3f}ms.")
        lines+=['','위 값은 탐지 latency와 따로 잰 순수 함수 비용이다. preview는 승인 성과가 아니며 P95를 단순 합산해 전체 SLO로 주장하지 않는다. B0 추론 표는 native Gate/prepare까지 포함하며 C1/C2/B1은 탐지+원본 후처리까지만 포함한다. 모델/규칙 내부 시간 개별 분리는 미계측이며 통합 탐지 구간과 공통 Resolver/HMAC 비용을 구분한다.']
    if c:
        lines+=['','동일 Mac CPU·동일 A/B 문서 순서, torch/OMP/MKL 2 threads. B0 ONNX intra/inter-op도 2로 고정했다. 후보는 순차 측정하므로 시스템 부하/열 상태까지 통제한 교차 실험은 아니다. 기존 캐시 replay ms를 추론 시간으로 쓰지 않았다. cold는 합성 첫 요청 로딩 1회 관측, warm은 다음 실제 corpus 요청이다. RSS는 worker process peak로 allocator/로딩 비용을 포함한다.','',
            '| 구성/데이터 | P50/P95 ms | RSS MiB | cold load ms | 실제 NER forward 합계 |','| --- | --- | ---: | ---: | --- |']
        for n,sets in c['latency'].items():
            for ds,t in sets.items():lines.append(f"| {n}/{ds}({t['n']}) | {t['p50_ms']:.2f}/{t['p95_ms']:.2f} | {t['peak_rss_mb']:.2f} | {t['cold_load_ms']:.2f} | {json.dumps(t['forwards'])} |")
        lines+=['','C1/C2/B1/B0의 A 실제 출력 대 cached 출력 불일치: '+json.dumps(c['parity'])+'. C1은 KLUE 한 체크포인트만 호출하고 확률 앙상블 추가 forward도 없다. C2는 AX 한 체크포인트만 호출한다. 원본 창 guard 일치를 위한 Veil tokenizer만 읽으며 Veil 모델은 생성·forward하지 않는다. 긴 문서는 여러 창 때문에 요청당 forward가 1회를 넘는다. KoE5 embedding은 NER 개수와 별도다.','',
            'forward 표는 warm corpus의 계측 합계다. 합성 warm-up 요청은 각 후보별 별도1회이며 corpus 합계에서 제외했다. B0 cold ONNX export/검증 과정의 PyTorch tracing forward 횟수는 미계측(BLOCKED)이라 0회로 보고하지 않는다. 요청별 NER 체크포인트 상한은 cold와 독립 검사 모두 AX+Veil 두 개이며 export는 동일 Veil checkpoint다.',
            'B2는 동일 A/Gold/SHA 예측 파일로 점수만 재생했다. 4팀 원본과 revision·asset SHA 일치가 입증된 atonlee 체크포인트가 없어 같은 Mac 실추론 속도는 **BLOCKED**다. 로컬 koelectra_ft5를 동일 원본으로 임의 대체하지 않았다. 다른 컴퓨터의 기존 35.8ms를 속도 배수 계산에 쓰지 않았다.','',
            '## B: 고정 후 노출된 분포 이동 검증','',
            'A에서 C1을 탐지부 finalist로 고정하고 B에서 한 번 평가했다. B는 171문서/1439 Gold, 이미 노출된 데이터이며 fresh Holdout2가 아니다. B 결과로 규칙·임계값·후보를 조정하지 않았다. C2/B1/B2의 B 점수는 미측정이며 우위를 추정하지 않는다.']
        if (PRIVATE/'C_native.json').exists():
            native=read('C_native.json')['B'];nv=native['detector']
            lines+=['',f"같은 B에서 원본 B0의 Gate 전 detector TP/FP/FN {nv['tp']}/{nv['fp']}/{nv['fn']}, F1 {nv['f1']:.6f}. 단일 C1과의 탐지부 동등 비교는 이 값으로 한다."]
        v=c['B'];lines+=['',f"C1 B: TP/FP/FN **{v['tp']}/{v['fp']}/{v['fn']}**, F1 **{v['f1']:.6f}**; 제안 보호 {v.get('plan_protected',0)}/1439; 공통 어댑터 READY {v.get('LOCAL_PREPARED',0)}, REVIEW {v.get('REVIEW_REQUIRED',0)}, BLOCKED {v.get('materialization_blocked',0)}. 실제 READY Gold {v.get('actual_ready_protected',0)}/{v.get('ready_gold',0)}, 실제 HMAC TP/FP {v.get('actual_hmac_tp',0)}/{v.get('actual_hmac_fp',0)}.",
            '기존 B0 B Gate typed F1 .946959는 다른 단계의 출력이므로 C1 Raw/후처리 점수와 우월성 비교하지 않는다. B0 B 실행은 동일 조건의 장문 비용/기준선 준비 계측이며 B 기반 정책 변경에 쓰지 않는다.']
    if d:
        lines+=['','## Phase D: 우승 안전 기준선 한 개의 격리 E2E','',
            'B0만 연결했다. 문서·질의는 같은 정책/키로 새로 준비하고 독립 검사 통과분만 실제 KoE5에 넣어 인메모리 색인했다. 운영 DB의 과거 가명 문서와 혼용하지 않았다. HCX는 기존 HCX-007 Mock schema 경로다.','',
            f"합성 3문서 smoke PASS, 정상 Mock 1회, 개인정보/임의 토큰/잘못된 토큰 반례 3개 차단. 실제 187문서 처리: {json.dumps(d['documents'])}; 보류 사유 {json.dumps(d['document_failures'])}.",
            '', '| 88질의 최초 실패/완료 단계 | 건수 |','| --- | ---: |']
        for stage,n in d['stages'].items():lines.append(f'| {stage} | {n} |')
        lines+=['',f"사유: `{json.dumps(d['errors'])}`. 실제 검색 가능한 경로의 Hit@5 분자/전체 분모는 {d['actual_hit5_numerator']}/88, 반환 Top5만 측정했다. 검색 전 차단 질의를 성공으로 세지 않았다. Context/Identity 실패를 탐지 F1로 해석하지 않는다.",
            '',f"독립 재검사는 선택한 AX+Veil의 raw 출력과 기존 구조 검사를 사용한다. 별도 KLUE/KoELECTRA를 숨겨 호출하지 않는다. 실제 추가 forward {json.dumps(d['model_forwards'])}, 요청/재사용 {json.dumps(d['counters'])}. 동일 가명 입력의 run 내 정확 snapshot 재사용을 명시했으며 cache hit를 추론 latency로 보고하지 않는다.",
            '',f"88질의 혼합 경로 지연 P50/P95 {d['e2e_latency']['p50_ms']:.2f}/{d['e2e_latency']['p95_ms']:.2f}ms. guard cache·최초 실패 단계가 섞인 진단값이며 전체 성공 응답 SLO/속도 개선 주장으로 쓰지 않는다.",
            '', '차단 원인 오프라인 재구성: 토큰 절단1질의에서 bounded 문서2개의 보호 토큰이 잘리는 것을 확인했다. 원본 hcx의 1500자 절단 이후 ContextBuilder가 전송 전에 막았다. 스키마 실패1건은 지역 불일치 top1을 고정 Mock이 인용해 REGION_MISMATCH_CITATION으로 거절된 것이다. 기존 합성 regression 두 경로도 PASS이며 검사 해제로 통과시키지 않았다. 원본 hcx/V6를 변경하지 않고 별도 토큰 안전 excerpt/Mock citation 과제로 남겼다. 88질의를 반복하지 않았다.',
            '', 'Mock transport는 성공41회+스키마 실패1회+합성1회=43회다. 최초 계수에서 빠진 스키마 실패 호출은 기록된 terminal stage와 코드 경로로 보정했고 finally에서 실패 호출도 세도록 계측을 수정했다. 유료 HCX 호출은 0회다.',
            '', '따라서 과거 quality4 guard+운영 DB의 10/88과는 guard 구성·색인 대상이 다르며 단순 통과율 개선으로 주장하지 않는다. Guard/Identity/출력 검사를 해제하지 않았다. 검증된 소유 관계가 없는 동명이인 후보는 그대로 CLARIFY한다. 실제 사실성·정답성·비공개 개인정보 잔류 여부의 수동 검수는 미완료다.']
    lines+=['','## 채택·폐기·BLOCKED','',
        '- 유지: 원본 Frozen V6 보호 계약/96-bit HMAC/typed namespace, KoE5/own_v6, 독립 잔여정보 검사·보수적 Identity.',
        '- 탐지부 후속 추천: C1 한 모델. B1보다 FP8 감소, 모델1개 감소. A 보호 부족1개는 PHONE이며 미승인 후보는 PERSON400, RRN31, CARD9(잔여 Mask-Only 구간 별도)이다. 안전한 승인 어댑터 개선 전에는 운영 교체하지 않는다.',
        '- C2 주소/번호 순수 함수는 실측 유효. PERSON 보정은 A 이득0이라 제외. 무조건 이 삭제·성씨/어휘 차단 목록·최장 경계 선택을 신규 정책에 추가하지 않았다.',
        '- 공통 adapter 채택 FAIL: 문맥 근거 부족과 Mask-Only 보류로 READY 손실, F0 보호 하한 동등성 실패. 처리율을 높이기 위한 자동 승인/일반어 whitelist 없음.',
        '- C3/C4/cascade 미승격: 단일 C1 탐지 F1은 B2와 동등 허용폭 내, C2 주소는52/52이며 추가 후보가 현재 승인 병목을 해결할 증거 없음.',
        '- BLOCKED: 4팀 같은 Mac live latency / 최소 필요: 원본 atonlee checkpoint와 revision·asset SHA. 새 NER 다운로드는 이번 범위 밖.',
        '- BLOCKED: 새 비노출 Holdout2 일반화 / 최소 필요: 잠금 이후 별도 출처·중복 검증한 미공개 labeled set. 현재 B를 fresh blind로 재명명하지 않음.',
        '- BLOCKED: 실제 답변 사실성·미라벨 잔여 PII 수동 검수 / 최소 필요: Git 외부 진단과 평가자 검수. 실제 HCX 유료 호출은 실행하지 않음.','',
        '## 재현·검증·보존','',
        '단위 회귀: Unified 95 PASS; 기존 Step1 30 PASS/1 SKIP, Step2 183 PASS, root29 PASS. 합계 **337 PASS / 1 SKIP / 0 FAIL**. Step1 SKIP은 기존 release notebook 부재다. 개인 절대경로에 의존하던 참조 chunk/merge 테스트는 고정 원본 함수의 독립 fixture로 교체했다. B0 ONNX 계측 첫 실행은 positional sess_options 중복 TypeError가 발생해 wrapper를 수정하고 해당 미완료 후보부터 재개했다. 성공한 C1/C2/B1의 실제 측정은 재생 시간으로 바꾸지 않고 원래 기록을 유지했다.','',
        '집계 CSV는 정확히 4개: `A_same_scorer.csv`, `A_error_taxonomy.csv`, `A_second_model_value.csv`, `C_pareto.csv`. 상세 Raw/Gold 오류 위치/입력/키/매핑은 Git 밖 private 디렉터리0700·파일0600. 보고서는 이 한 파일만 추가한다. 실제 환경·원본 V6/main/운영 DB/HMAC 키·팀원/4팀 원본은 변경하지 않았다. SQL 쓰기·유료 HCX·외부 개인정보 전송·PR·merge 0회. 실험 중 push는 하지 않았으며, 종료 후 사용자 별도 요청에 따라 feature/unified-v1에만 결과를 커밋·push한다. Phase D 이후 실험을 중단한다.','',
        '```bash','cd /Users/gojunseo/AegisRAG-unified-v1','export PYTHONPATH=step2_privacy_rag/app','export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false',
        'LEAN_MODEL_PY=/tmp/aegis-unified-followup-model-env/bin/python','LEAN_RETRIEVAL_PY=/Users/gojunseo/opt/anaconda3/envs/aegisrag-retrieval/bin/python',
        '"$LEAN_MODEL_PY" -m unified_v1.lean A','"$LEAN_MODEL_PY" -m unified_v1.lean B','"$LEAN_MODEL_PY" -m unified_v1.lean C',
        '"$LEAN_RETRIEVAL_PY" -m unified_v1.lean D --runtime-python "$LEAN_MODEL_PY"','"$LEAN_MODEL_PY" -m unified_v1.lean report',
        'PYTHONPATH=step2_privacy_rag/app:tests/unified_v1 "$LEAN_MODEL_PY" -m unittest discover -s tests/unified_v1 -q','```','',
        '위 재현은 기존 A/B 검증 반복이며 새 독립 검증이 아니다. C 중단 후에는 입력·코드·참조·모델 hash가 일치하는 완료된 실제 추론 기록으로만 재개한다. 이번 최초 측정은 계측 wrapper/회귀 정리 전 실측이며 최종 source와 구분한다. 최초 기록에는 이 resume seal이 없어 재현 C는 실제 후보를 다시 측정한다. 전량 새 비용 측정이 필요하면 private C_latency/C_live_*만 별도 백업 후 제거한다. 원문 상세는 터미널에 출력하지 않는다.','',
        '### SHA 기록','',f"- 시작 Git: `{m['head']}`",f"- 팀원 참조: `{m['reference_head']}`",f"- 4팀 참조: `{m['four_team_head']}`",f"- A 원문·Gold JSONL: `{m['A_sha']}`",f"- B 원문·Gold JSONL: `{m['B_sha']}`",f"- 동일 strict scorer(selection.py): `{m['scorer_sha']}`",f"- 최종 lean_policy.py: `{S.digest(Path(__file__).with_name('lean_policy.py'))}`",f"- 최종 lean_worker.py: `{S.digest(Path(__file__).with_name('lean_worker.py'))}`",f"- 최종 lean.py: `{S.digest(__file__)}`",'- 모델 실행 환경: torch2.2.2 / transformers4.55.4 / tokenizers0.21.4 / numpy1.26.4, 기존 별도 임시 venv를 재사용. 새 설치·기존 환경 업그레이드 없음.', '- KoE5: `nlpai-lab/KoE5@237b580aea358e3042fcf5baf78124c33c3afd16` (1024d/512 tokens/query:·passage:/normalized).','']
    for name,model in m['own_models'].items():lines.append('- '+name+': `'+str(model)+'`')
    for name,hashes in m['model_hashes'].items():
        for asset,sha in hashes.items():lines.append(f'- {name}/{asset}: `{sha}`')
    target=ROOT/'docs/unified_v1/LEAN_FINAL_DECISION.md';target.write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
