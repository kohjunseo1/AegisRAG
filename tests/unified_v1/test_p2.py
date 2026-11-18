import unittest
from unified_v1.contracts import Candidate, ContractError, fingerprint
from unified_v1.adapters import DetectionEngine, AxAdapter, VeilAdapter
from unified_v1.protection import (fuse, prepare, covers, StructuralSentinel, ContextualBoost, V6GateAdapter, READY)

KEY = b'unified-synthetic-only-key-00000000'
from dataset_preparation import key_tag as frozen_key_tag
TAG = frozen_key_tag(KEY)

def evidence(text, spans=(), kind='document'):
    return DetectionEngine('own2', {'ax':AxAdapter('mock',lambda t:spans), 'veil':VeilAdapter('mock',lambda t:[])}).detect(text,input_kind=kind)

def baseline(spans=(), masks=None):
    return dict(decision=READY, entities=list(spans), entities_for_pseudonymization=list(spans),
                mask_intervals=masks if masks is not None else [dict(start=s['start'],end=s['end']) for s in spans],redaction_intervals=[])

class ProtectionTests(unittest.TestCase):
    def test_union_preserves_baseline_and_new_conflicting_candidates(self):
        text='😀금 재원 안내'
        base=[dict(label='person',start=1,end=5)]
        raw=[dict(label='address',start=3,end=6)]
        p=fuse(text,evidence(text,raw),baseline(base))
        self.assertTrue(covers((1,6),p.mask_intervals))
        self.assertIn('LABEL_CONFLICT',p.review_reason_codes)
        self.assertFalse(p.entities_for_hmac)
        self.assertEqual(prepare(text,p,input_kind='document',hmac_key=KEY,key_tag=TAG).state,'REVIEW')

    def test_contextual_patterns_remain_review(self):
        for name in ['금 재원','선우','탁 윤','남궁수','재원','선우진']:
            text=name+' 님 합성 안내'
            self.assertTrue(ContextualBoost().candidates(text))
            p=fuse(text,evidence(text),baseline())
            self.assertTrue(p.unresolved_mentions)
            self.assertTrue(p.review_reason_codes)

    def test_structural_sentinel_partial_address_postcode_and_business_id(self):
        text='주소 서울시 강남구\n우편번호: 12345\n업무 ID: 12345\n전화 010-0000-0000'
        rows=StructuralSentinel().candidates(text)
        self.assertTrue(any('POSTCODE_CONTEXT' in r.evidence_codes for r in rows))
        self.assertFalse(any(r.start <= text.index('업무 ID') < r.end for r in rows))
        p=fuse(text,evidence(text),baseline())
        self.assertTrue(all(covers((r.start,r.end),p.mask_intervals) for r in rows))
        self.assertIn('UNCONFIRMED_CANDIDATE',p.review_reason_codes)

    def test_long_line_tail_and_baseline_only_mask(self):
        text='합성 '*2000+'010-0000-0000'
        p=fuse(text,evidence(text),baseline(masks=[dict(start=0,end=2)]))
        self.assertTrue(covers((0,2),p.mask_intervals))
        self.assertTrue(covers((len(text)-13,len(text)),p.mask_intervals))

    def test_mask_only_is_local_unapproved(self):
        text='합성 값'
        p=fuse(text,evidence(text),baseline(masks=[dict(start=0,end=2)]))
        result=prepare(text,p,input_kind='document',hmac_key=KEY,key_tag=TAG)
        self.assertEqual(result.protection_kind,'MASKED_ONLY')
        self.assertEqual(result.approval_state,'NOT_APPROVED')
        self.assertNotIn('합성',result.sanitized_text)

    def test_missing_model_and_wrong_input_block(self):
        ev=DetectionEngine('quality4',{}).detect('합성',input_kind='query')
        p=fuse('합성',ev,baseline())
        self.assertEqual(prepare('합성',p,input_kind='query',hmac_key=KEY,key_tag=TAG).state,'BLOCKED')
        with self.assertRaises(ContractError):prepare('다름',p,input_kind='query',hmac_key=KEY,key_tag=TAG)
        with self.assertRaises(ContractError):fuse('합성',evidence('합성'),baseline(masks=[dict(start=0,end=99)]))

    def test_frozen_gate_adapter(self):
        class Fake:
            def detect(self,text):return dict(status='READY_FOR_PSEUDONYMIZATION',entities=[],mask_intervals=[])
            def ax_predict(self,text):return []
            def veil_predict(self,text):return []
        self.assertEqual(V6GateAdapter(Fake()).check('안내')['decision'],READY)
        p=V6GateAdapter(Fake()).plan('안내',evidence('안내'))
        self.assertFalse(p.review_reason_codes)

    def test_repeat_spacing_and_particle_expand_protection_without_identity_merge(self):
        text='고객명: 금재원\n금 재원이 신청했습니다.'
        start=text.index('금재원');spans=[dict(label='person',start=start,end=start+3)]
        p=fuse(text,evidence(text,spans),baseline(spans))
        repeated=text.index('금 재원')
        self.assertTrue(covers((repeated,repeated+4),p.mask_intervals))
        self.assertIn('UNCONFIRMED_CANDIDATE',p.review_reason_codes)
        self.assertTrue(any('REPEATED_NAME_CANDIDATE_ONLY' in c.evidence_codes for c in p.unresolved_mentions))
