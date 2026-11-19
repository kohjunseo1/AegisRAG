"""Resident local-only lean candidates; explicit replay vs real forward accounting."""
import ast
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
import types
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unified_v1.lean_policy import corrections, checked



def configure_pipeline(pipeline):
    pipeline.HYBRID=False;pipeline.ENSEMBLE=False
    pipeline.BOOST_ADDRESS=pipeline.BOOST_PERSON=True


def finish_team(candidate,text,base,hybrid,auxiliary=None):
    if candidate=='C1':
        spans,dropped=hybrid.post_filter(text,base)
        return spans,[],dropped,[]
    if candidate!='B1' or auxiliary is None:raise ValueError('INVALID_TEAM_CANDIDATE')
    ks=auxiliary(text)
    spans,review=hybrid.combine(text,base,ks,kiwi=None,tau_accept=.9,tau_review=.5,name_filter='strict')
    return spans,review,[],ks


def reference_modules(reference):
    sys.path.insert(0,str(reference))
    config=types.ModuleType('starter.config');config.ROOT=reference;config.HMAC_KEY=''
    sys.modules['starter.config']=config
    from starter import pii_pipeline as P, detect_boost as boost, hybrid_detector as H
    configure_pipeline(P)
    return P,boost,H


def ax_only():
    """Execute pinned backend's original AX body only, with both tokenizer guards.

    No Veil model allocation/export/forward. AST selection asserts source anchors;
    original decoder, tokenization and windowing stay unchanged.
    """
    import pii_detector.backends as B
    from unified_v1.replay import FrozenWindowPredictor
    path=Path(B.__file__);tree=ast.parse(path.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_backends')
    cutoff=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='veil_tok' for t in n.targets))
    fn.body=fn.body[:cutoff]
    fn.body+=ast.parse("veil_tok=load_fast_tokenizer(VEIL_DIR)\ndef token_guard(piece):\n return len(ax_tok.encode(piece,add_special_tokens=True,truncation=False))<=512 and len(veil_tok.encode(piece,add_special_tokens=True,truncation=False))<=512\nreturn predict_ax,token_guard").body
    ast.fix_missing_locations(fn);ns=dict(B.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
    manifest=json.loads(path.with_name('model_manifest.json').read_text())
    predict,guard=ns['load_backends'](manifest)
    # AX network resides in predict closure; instrumentation counts every window.
    net=next(cell.cell_contents for cell in predict.__wrapped__.__closure__ if hasattr(cell.cell_contents,'register_forward_hook'))
    return FrozenWindowPredictor(predict,guard),net


def main():
    reference=Path(sys.argv[1]);candidate=sys.argv[2]
    def denied(*a,**kw):raise RuntimeError('LEAN_NETWORK_FORBIDDEN')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    counts={};loaded=False;cold=0;predictors={};P=boost=H=None
    for line in sys.stdin:
        try:
            req=json.loads(line);text=req['text'];replay=req.get('replay',False)
            if not isinstance(text,str) or len(text)>20000:raise ValueError()
            with open(os.devnull,'w') as null,contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
                started=time.perf_counter()
                if P is None:
                    P,boost,H=reference_modules(reference);original_model_predict=P.model_predict
                if not replay and not loaded:
                    import torch
                    torch.set_num_threads(2);torch.set_num_interop_threads(2)
                    from unified_v1.adapters import LocalHFPredictor
                    from unified_v1.runtime import KLUE_HASHES,KOELECTRA_HASHES
                    root=Path.home()/'private-models/aegisrag'
                    def track(name,net):
                        counts[name]=0
                        net.register_forward_hook(lambda *a:counts.__setitem__(name,counts[name]+1))
                    if candidate=='B0':
                        import onnxruntime as ort
                        real_run=ort.InferenceSession.run
                        def counted_run(obj,*a,**kw):
                            counts['veil']=counts.get('veil',0)+1
                            return real_run(obj,*a,**kw)
                        ort.InferenceSession.run=counted_run
                        # Preserve original decoder but fix intra/inter-op threads.
                        real_init=ort.InferenceSession.__init__
                        def bounded_init(obj,*a,**kw):
                            args=list(a)
                            opts=(args[1] if len(args)>1 else kw.get('sess_options')) or ort.SessionOptions()
                            opts.intra_op_num_threads=2;opts.inter_op_num_threads=2
                            if len(args)>1:args[1]=opts;kw.pop('sess_options',None)
                            else:kw['sess_options']=opts
                            return real_init(obj,*args,**kw)
                        ort.InferenceSession.__init__=bounded_init
                        from unified_v1.runtime import load_own_predictors
                        predictors=load_own_predictors()
                        ax_predict=predictors['ax'].predictor
                        net=next(c.cell_contents for c in ax_predict.__wrapped__.__closure__ if hasattr(c.cell_contents,'register_forward_hook'))
                        track('ax',net)
                    if candidate in ('C2','C3','C4'):
                        predictors['ax'],net=ax_only();track('ax',net)
                    if candidate in ('C1','B1'):
                        k=LocalHFPredictor(str(root/'roberta_small_v1'),str(root),KLUE_HASHES)
                        k.model.config.return_dict=True;k.model.config.torchscript=False
                        P._tok=k.tok;P._model=k.model;track('klue',k.model)
                    if candidate=='B1':
                        from starter.hf_detector import HFDetector,KOELECTRA_MAP
                        from collections import Counter
                        k=LocalHFPredictor(str(root/'koelectra_ft5'),str(root),KOELECTRA_HASHES,label_policy='koelectra59')
                        original=HFDetector.__new__(HFDetector);original.torch=torch;original.tok=k.tok;original.model=k.model;original.id2label=k.model.config.id2label;original.mapping=KOELECTRA_MAP;original.unmapped=Counter()
                        predictors['koelectra']=original.predict;track('koelectra',k.model)
                    loaded=True;cold=(time.perf_counter()-started)*1000
                counts.update({s:0 for s in counts});started=time.perf_counter();raw=[];review=[];dropped=[];by={}
                if replay:
                    raw=req['raw']
                    if candidate in ('C1','B1'):
                        P.model_predict=lambda value:copy.deepcopy(raw)
                if candidate=='B0':
                    from unified_v1.contracts import Candidate,DetectionEvidence,config_hash,fingerprint
                    from unified_v1.selection import frozen_outcome
                    by={source:predictors[source](text) for source in ('ax','veil')}
                    fp=fingerprint(text)
                    ev=DetectionEvidence('document','lean',{source:tuple(Candidate(source,'pinned',r['label'],r['start'],r['end'],fp,evidence_codes=tuple(r.get('evidence_codes',()))) for r in rs) for source,rs in by.items()},{'ax':'OK','veil':'OK'},(),config_hash({'lean':'B0'}),fp,'own2')
                    frozen=frozen_outcome(text,ev)
                    spans=[dict(label=l,start=a,end=b) for l,a,b in frozen['pred']]
                    raw=by['ax']+by['veil']
                elif candidate in ('C1','B1'):
                    if not replay:P.model_predict=original_model_predict
                    raw=copy.deepcopy(raw) if replay else P.model_predict(text)
                    # Keep the exact model_predict -> P.detect -> post_filter order,
                    # reusing this request's model output without a second forward.
                    real=P.model_predict;P.model_predict=lambda value:copy.deepcopy(raw)
                    try:base=P.detect(text)
                    finally:P.model_predict=real
                    auxiliary=(lambda value:req['koelectra']) if replay else predictors.get('koelectra')
                    spans,review,dropped,ks=finish_team(candidate,text,base,H,auxiliary)
                    raw=raw+ks
                else:
                    if not replay:raw=predictors['ax'](text)
                    spans=corrections(text,raw,boost,P,enabled=tuple(req.get('enabled',['person','address','numbers'])))
                elapsed=(time.perf_counter()-started)*1000
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
            print(json.dumps(dict(spans=checked(text,spans),raw=checked(text,raw),review=review,dropped=dropped,
                by_source=by,mode='REPLAY' if replay else 'REAL_INFERENCE',inference_ms=elapsed,cold_load_ms=cold,
                forwards=dict(counts),peak_rss_mb=rss)),flush=True)
        except Exception as exc:
            import traceback
            frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)]
            print(json.dumps(dict(error='LEAN_WORKER_FAILED',error_type=type(exc).__name__,frames=frames)),flush=True)
            return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
