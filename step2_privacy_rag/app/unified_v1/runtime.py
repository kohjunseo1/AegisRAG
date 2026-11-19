"""Explicit local runtime factories and honest model readiness/latency probes."""
from pathlib import Path
import json
import statistics
import time
from .contracts import ContractError, PROFILES, config_hash, fingerprint
from .adapters import AxAdapter, VeilAdapter, KlueAdapter, KoElectraAdapter, PersistentPredictor, DetectionEngine

KLUE_HASHES={
 'config.json':'d8d486cc3d59fec1ff109eaf6a60e22b53dc451d4dccb14f94e4b93fcf0b07ac',
 'model.safetensors':'05dd28640e2c884007b7c7668ad1e2f770ac2cabe0d31cc9cfe6ac02d2b69b4f',
 'tokenizer.json':'200c2fd5a65daf3b67aa532740d3a26736deceaa8b28e82826038aede9377cf0',
 'tokenizer_config.json':'fd172592aef04c3c1c5be31fa2072c27ae81bdc4192b4e919ea0db6b43407913'}

KOELECTRA_HASHES={
 'config.json':'6eb2b024f6144b5df04be1304997765aa8518420998ab1f3151a50f570ba0f91',
 'model.safetensors':'fd2a89454ef29acac263ce3ea3629ee6b5eb513216d46f599462e5b179922abc',
 'tokenizer.json':'6234fb96e9031ec5e6a83b27ba1ade973e823d5e34a62a49c8d00a20f49e09eb',
 'tokenizer_config.json':'8a4a9ae2d88ba2638f862c7df7564b38ac4b2849f24c850752a72b9a1e3908b8'}

def load_own_predictors(cache_dir=None):
    from .replay import FrozenWindowPredictor
    from pii_detector.backends import load_backends
    path=Path(__file__).resolve().parents[3]/'step1_pii_detector/src/pii_detector/model_manifest.json'
    manifest=json.loads(path.read_text())
    ax,veil,guard=load_backends(manifest,cache_dir=cache_dir)
    return {'ax':FrozenWindowPredictor(ax,guard),'veil':FrozenWindowPredictor(veil,guard)}

class OwnPairSession:
    """One request-scoped pair result from one persistent process, no stale text cache."""
    def __init__(self,worker,sources):
        self.sources=tuple(sources)
        self.worker=worker;self.current=None;self.results=None
    def begin(self,text):
        self.current=fingerprint(text);self.results=None
    def predict(self,source,text):
        if self.current!=fingerprint(text):raise ContractError('REQUEST_SESSION_MISMATCH')
        if self.results is None:
            self.results=(self.worker(text,sources=self.sources) if isinstance(self.worker,PersistentPredictor) else self.worker(text))
        return self.results[source]
    def end(self):self.current=None;self.results=None

class ReusableDetectionEngine(DetectionEngine):
    def __init__(self,*args,own_session=None,**kwargs):
        super().__init__(*args,**kwargs)
        import threading
        self.own_session=own_session;self._session_lock=threading.Lock()
    def detect(self,text,**kwargs):
        with self._session_lock:
            if self.own_session:self.own_session.begin(text)
            try:return super().detect(text,**kwargs)
            finally:
                if self.own_session:self.own_session.end()

def build_engine(profile, *, own_worker=None, klue_worker=None,koelectra_worker=None):
    if profile not in PROFILES:raise ContractError('UNKNOWN_PROFILE')
    adapters={};session=None
    if own_worker is not None:
        session=OwnPairSession(own_worker,[s for s in PROFILES[profile] if s in {'ax','veil'}])
        manifest=json.loads((Path(__file__).resolve().parents[3]/'step1_pii_detector/src/pii_detector/model_manifest.json').read_text())
        for source,cls in [('ax',AxAdapter),('veil',VeilAdapter)]:
            if source in PROFILES[profile]:
                adapters[source]=cls(manifest[source]['revision'],lambda t,s=source:session.predict(s,t))
    if klue_worker is not None and 'klue' in PROFILES[profile]:
        adapters['klue']=KlueAdapter(config_hash(KLUE_HASHES),klue_worker)
    if koelectra_worker is not None and 'koelectra' in PROFILES[profile]:
        adapters['koelectra']=KoElectraAdapter(config_hash(KOELECTRA_HASHES),koelectra_worker)
    return ReusableDetectionEngine(profile,adapters,own_session=session)

def profile_status(profile, validated_sources):
    required=PROFILES[profile]
    if 'koelectra' in required and validated_sources.get('koelectra') not in {'LOCAL_MODEL_PASS','MOCK_ONLY'}:return 'MISSING'
    return 'REAL_READY' if all(validated_sources.get(s)=='LOCAL_MODEL_PASS' for s in required) else 'BLOCKED'

def benchmark_predictor(predictor,text, *, repeats=30):
    if repeats<30:raise ContractError('WARM_SAMPLE_TOO_SMALL')
    predictor(text)
    timings=[]
    for _ in range(repeats):
        started=time.perf_counter();predictor(text);timings.append((time.perf_counter()-started)*1000)
    return dict(samples=repeats,warm_p50_ms=statistics.median(timings),
                warm_p95_ms=sorted(timings)[__import__('math').ceil(.95*repeats)-1],
                **getattr(predictor,'last_metrics',{}))
