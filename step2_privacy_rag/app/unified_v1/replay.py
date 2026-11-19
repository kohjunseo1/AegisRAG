"""Frozen V6 policy evaluated against request-owned raw snapshots, never live heads."""
from pathlib import Path
import sys
from .contracts import ContractError, PROFILES, fingerprint

# Same repo-local dependency as V6 entrypoints; no environment/model mutation.
_src=str(Path(__file__).resolve().parents[3]/'step1_pii_detector/src')
if _src not in sys.path:sys.path.insert(0,_src)
from pii_detector.detector import PIIDetector, chunk_windows
from privacy_gate import check_privacy_gate

class FrozenWindowPredictor:
    def __init__(self,predictor,token_guard=None):
        self.predictor,self.token_guard=predictor,token_guard

    def __call__(self,text):
        if len(text)>20000:raise ContractError('INPUT_EXCEEDS_MAX_CHARS')
        windows=([dict(window_start=0,window_end=len(text),core_start=0,core_end=len(text),text=text)]
                 if len(text)<=600 and (self.token_guard is None or self.token_guard(text)) else chunk_windows(text))
        if len(windows)>150 or (self.token_guard and any(not self.token_guard(w['text']) for w in windows)):
            raise ContractError('TOKEN_WINDOW_LIMIT')
        with PIIDetector(self.predictor,self.predictor,parallel=False) as policy:
            rows,warnings=policy._predict_all_windows(self.predictor,windows,text)
        edges={(w['start'],w['end']) for w in warnings}
        return [{**r,'evidence_codes':('WINDOW_EDGE',) if (r['start'],r['end']) in edges else ()} for r in rows]

class SnapshotDetector(PIIDetector):
    def __init__(self,text,evidence):
        self._text_hash=fingerprint(text)
        self._rows={s:[dict(label=c.label,start=c.start,end=c.end) for c in rows]
                    for s,rows in evidence.raw_candidates_by_source.items()}
        self._edges={s:[dict(code='entity_touches_chunk_edge',start=c.start,end=c.end)
                        for c in rows if 'WINDOW_EDGE' in c.evidence_codes]
                     for s,rows in evidence.raw_candidates_by_source.items()}
        def ax(t):return self._head('ax',t)
        def veil(t):return self._head('veil',t)
        super().__init__(ax,veil,parallel=False)

    def _head(self,source,text):
        if fingerprint(text)!=self._text_hash:raise ContractError('SNAPSHOT_INPUT_MISMATCH')
        return [dict(r) for r in self._rows[source]]

    def _predict_all_windows(self,fn,windows,doc):
        source='ax' if fn is self.ax_predict else 'veil'
        return self._head(source,doc),list(self._edges[source])

class SnapshotGateAdapter:
    def __init__(self, *, policy='evidence_v1'):
        self.policy=policy

    def check(self,text,evidence):
        if evidence.original_input_fingerprint!=fingerprint(text):
            return dict(decision='BLOCKED',reason='snapshot_input_mismatch')
        try:
            for rows in evidence.raw_candidates_by_source.values():
                for candidate in rows:candidate.validate(text)
        except Exception:return dict(decision='BLOCKED',reason='snapshot_validation_failed')
        if not evidence.ready:return dict(decision='BLOCKED',reason='required_model_failed')
        if not {'ax','veil'}<=set(PROFILES[evidence.detector_profile]):
            # No hidden own2 inference; equivalent safety signals not established.
            return dict(decision='BLOCKED',reason='profile_gate_signals_unavailable')
        with SnapshotDetector(text,evidence) as detector:
            result=detector.detect(text)
            gate=check_privacy_gate(detector,text)
            if gate.get('decision')!='READY_FOR_LOCAL_PSEUDONYMIZATION':
                gate=dict(gate)
                gate['proposed_protection']=list(gate.get('proposed_protection',[]))+list(result.get('mask_intervals',[]))
                return gate
            # Reuse V6 name-policy protection too, without re-inference or ID heuristics.
            from name_protection_policy import decide
            from name_boundary_policy import propose
            from name_protection_handoff import _protection_plan
            decision=decide(text,result,'v5_privacy_first')
            if decision.get('would_abstain'):return dict(decision='REVIEW_REQUIRED',reason='v5_abstain',proposed_protection=gate['mask_intervals'])
            try:
                gate,counts=_protection_plan(text,result,gate,decision,propose(text,result))
                if counts.get('malformed_typed_count'):gate['decision']='REVIEW_REQUIRED'
            except Exception:
                return dict(decision='BLOCKED',reason='v6_policy_failed',proposed_protection=list(result.get('mask_intervals',[]))+list(gate.get('mask_intervals',[])))
            return gate

    def plan(self,text,evidence):
        from .protection import fuse
        gate=self.check(text,evidence)
        return fuse(text,evidence,gate,policy=self.policy) if self.policy!='evidence_v1' else fuse(text,evidence,gate)
