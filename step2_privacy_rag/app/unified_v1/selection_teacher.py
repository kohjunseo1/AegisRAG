"""Read-only reference replay in an isolated network-denied process.

One option per process, fixed before any request. Never imports reference .env,
search, pseudonymizer or transport. No reference files are modified.
"""
import contextlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import types
import copy
import hashlib

def main():
    reference=Path(sys.argv[1]);option=sys.argv[2]
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(reference))
    def denied(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
    socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.create_connection=denied
    # config.py otherwise loads the reference .env on import. Detection needs ROOT only.
    config=types.ModuleType('starter.config');config.ROOT=reference;config.HMAC_KEY=''
    sys.modules['starter.config']=config
    with open(os.devnull,'w') as null,contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
        import torch
        from unified_v1.adapters import LocalHFPredictor
        from unified_v1.runtime import KLUE_HASHES,KOELECTRA_HASHES
        from starter import pii_pipeline as P
        from starter.hf_detector import HFDetector,KOELECTRA_MAP
        from starter.hybrid_detector import combine
        from starter import model_ensemble as E
        torch.set_num_threads(2)
        root=Path.home()/'private-models/aegisrag'
        klue=LocalHFPredictor(str(root/'roberta_small_v1'),str(root),KLUE_HASHES)
        koe=LocalHFPredictor(str(root/'koelectra_ft5'),str(root),KOELECTRA_HASHES,label_policy='koelectra59')
        # Original P/model_ensemble require ModelOutput. This changes container only.
        enc=klue.tok('합성 입력',return_tensors='pt');enc.pop('token_type_ids',None)
        with torch.inference_mode():before=klue.model(**enc)[0].clone()
        klue.model.config.return_dict=True
        klue.model.config.torchscript=False
        with torch.inference_mode():after=klue.model(**enc).logits
        if not torch.equal(before,after):raise RuntimeError('RETURN_DICT_LOGITS_PARITY_FAILED')
        P._tok=klue.tok;P._model=klue.model
        P.HYBRID=False;P.ENSEMBLE=option!='no_ensemble'
        P.BOOST_ADDRESS=P.BOOST_PERSON=option!='no_boost'
        kiwi=None
        if option=='with_kiwi':
            from kiwipiepy import Kiwi
            kiwi=Kiwi(num_workers=2);P._kiwi=kiwi
        original=HFDetector.__new__(HFDetector)
        original.torch=torch;original.tok=koe.tok;original.model=koe.model
        original.id2label=koe.model.config.id2label;original.mapping=KOELECTRA_MAP
        from collections import Counter
        original.unmapped=Counter()
    cache_dir=Path.home()/'.cache/aegisrag/unified_v1/private/selection/teacher_raw'
    cache_dir.mkdir(parents=True,exist_ok=True);cache_dir.chmod(0o700)
    real_predict=P.model_predict;real_probs=E.person_probs;real_koe=original.predict
    for line in sys.stdin:
        try:
            text=json.loads(line)['text']
            if not isinstance(text,str) or len(text)>20000:raise ValueError()
            with open(os.devnull,'w') as null,contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
                started=time.perf_counter()
                cache_path=cache_dir/(hashlib.sha256(text.encode()).hexdigest()+'.json')
                replay=cache_path.exists()
                if replay:
                    raw=json.loads(cache_path.read_text())
                else:
                    raw=dict(klue=real_predict(text),person_probs=real_probs(text),koelectra=real_koe(text))
                    fd=os.open(cache_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                    with os.fdopen(fd,'w') as f:json.dump(raw,f)
                # These are exact original function outputs, not adapters' argmax
                # approximations. Same snapshots for every ablation; no flag mutation.
                P.model_predict=lambda value:copy.deepcopy(raw['klue']) if value==text else real_predict(value)
                E.person_probs=lambda value:copy.deepcopy(raw['person_probs']) if value==text else real_probs(value)
                base=P.detect(text)
                ks=copy.deepcopy(raw['koelectra'])
                spans,review=combine(text,base,ks,kiwi=kiwi,tau_accept=.9,tau_review=.5,
                    drop_unagreed=option=='drop_unagreed',num_union=option=='num_union',
                    name_filter='loose' if option=='name_loose' else 'none' if option=='name_none' else 'strict')
                ms=(time.perf_counter()-started)*1000
            print(json.dumps(dict(spans=spans,review=review,inference_ms=ms,raw_snapshot_reused=replay,return_dict_logits_parity=True)),flush=True)
        except Exception:
            print(json.dumps(dict(error='TEACHER_REPLAY_FAILED')),flush=True);return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
