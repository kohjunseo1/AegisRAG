from dataclasses import replace
import unittest
from unified_v1.contracts import Candidate,fingerprint
from unified_v1.protection import fuse,covers
from unified_v1.replay import SnapshotGateAdapter
from test_p2 import evidence,baseline

class SafetyRegressionTests(unittest.TestCase):
    def test_non_pii_is_not_confirmed_hmac(self):
        p=fuse('교육 안내',evidence('교육 안내'),baseline(),policy='evidence_v2')
        self.assertEqual(p.protection_status,'NO_PII_CANDIDATES')

    def test_four_source_added_coverage_never_shrinks_and_conflicts_review(self):
        from unified_v1.adapters import DetectionEngine,AxAdapter,VeilAdapter,KlueAdapter,KoElectraAdapter
        text='😀금 재원이 안내'
        raw=lambda t:[dict(label='person',start=1,end=5)]
        engines={a.source_id:a for a in [AxAdapter('m',raw),VeilAdapter('m',raw),KlueAdapter('m',lambda t:[]),KoElectraAdapter('m',lambda t:[dict(label='address',start=3,end=6)])]}
        ev=DetectionEngine('quality4',engines).detect(text,input_kind='document')
        p=fuse(text,ev,baseline([dict(label='person',start=1,end=5)]),policy='evidence_v2')
        self.assertTrue(covers((1,6),p.mask_intervals));self.assertEqual(p.protection_status,'REVIEW')
        self.assertFalse(p.entities_for_hmac)
