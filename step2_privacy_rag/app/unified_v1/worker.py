"""Resident, network-blocked local model worker. One JSON line per request."""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
if __package__ in {None,''}:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unified_v1.adapters import LocalHFPredictor
from unified_v1.contracts import config_hash, fingerprint
MAX_BYTES=1048576

def main():
    try:
        if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():return 2
        manifest=json.loads(Path(sys.argv[1]).read_text())
        if manifest['source_id'] not in {'klue','koelectra','own2'}:return 2
        def blocked(*args,**kwargs):raise RuntimeError('WORKER_NETWORK_FORBIDDEN')
        socket.socket.connect=blocked;socket.socket.connect_ex=blocked;socket.create_connection=blocked
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        predictor=None;cold_ms=None
        while True:
            line=sys.stdin.buffer.readline(MAX_BYTES+1)
            if not line:return 0
            if len(line)>MAX_BYTES:return 2
            request=json.loads(line)
            if (request['fingerprint']!=fingerprint(request['text']) or request['config_hash']!=config_hash(manifest)):
                return 2
            with open(os.devnull,'w') as null,contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
                if predictor is None:
                    started=time.perf_counter()
                    if manifest['source_id']=='own2':
                        from unified_v1.runtime import load_own_predictors
                        predictor=load_own_predictors(manifest.get('cache_dir'))
                    else:
                        predictor=LocalHFPredictor(manifest['model_dir'],manifest['trusted_root'],manifest['hashes'],
                                                  ignore_labels=manifest.get('ignore_labels',()),
                                                  max_length=manifest.get('max_length',512),stride=manifest.get('stride',64),
                                                  label_policy='koelectra59' if manifest['source_id']=='koelectra' else 'core7')
                    cold_ms=(time.perf_counter()-started)*1000
                started=time.perf_counter()
                if isinstance(predictor,dict):
                    requested=request.get('sources',list(predictor))
                    if not requested or not set(requested)<=set(predictor):return 2
                    rows={source:predictor[source](request['text']) for source in requested}
                else:rows=predictor(request['text'])
                inference_ms=(time.perf_counter()-started)*1000
                diagnostics={}
                if request.get('diagnostic_legacy'):
                    if not isinstance(predictor,dict):return 2
                    from unified_v1.experiments import legacy_diagnostic
                    diagnostics=legacy_diagnostic(predictor,request['text'],request.get('input_kind','document'))
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            metrics=dict(cold_load_ms=cold_ms,inference_ms=inference_ms,
                         peak_rss_mb=rss/(1024**2 if sys.platform=='darwin' else 1024),
                         ignored_label_token_counts=getattr(predictor,'ignored_label_counts',{}))
            response=dict(request_id=request['request_id'],fingerprint=request['fingerprint'],
                          config_hash=request['config_hash'],candidates=rows,metrics=metrics,diagnostics=diagnostics)
            output=json.dumps(response).encode()+b'\n'
            if len(output)>MAX_BYTES:return 2
            sys.stdout.buffer.write(output);sys.stdout.buffer.flush()
    except Exception:return 2

if __name__=='__main__':raise SystemExit(main())
