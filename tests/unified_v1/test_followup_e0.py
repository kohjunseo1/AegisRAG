"""Frozen own2 coverage/Gate/HMAC parity using fabricated data only."""
import re
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'step1_pii_detector/src'))
from pii_detector.detector import PIIDetector
from privacy_gate import check_privacy_gate
from local_pseudonymizer import pseudonymize_local
from document_preparation import prepare as v6_prepare
from dataset_preparation import key_tag
from unified_v1.adapters import AxAdapter, VeilAdapter, DetectionEngine
from unified_v1.protection import V6GateAdapter, prepare

class FrozenParityTests(unittest.TestCase):
    def test_own2_mask_gate_hmac_and_v6_preparation(self):
        key=b'followup-public-synthetic-key-00000'
        for text,label,value in [('연락: 010-0000-0000','phone_number','010-0000-0000'),
                                 ('이메일: fixture@example.invalid','email','fixture@example.invalid'),
                                 ('금재원 안내','person','금재원'),('교육 절차 안내',None,None)]:
            def raw(piece):
                return [dict(label=label,start=m.start(),end=m.end()) for m in re.finditer(re.escape(value),piece)] if value else []
            with PIIDetector(raw,raw,parallel=False) as detector:
                original=check_privacy_gate(detector,text)
                self.assertEqual(original['decision'],'READY_FOR_LOCAL_PSEUDONYMIZATION')
                ev=DetectionEngine('own2',{'ax':AxAdapter('mock',raw),'veil':VeilAdapter('mock',raw)}).detect(text,input_kind='document')
                plan=V6GateAdapter(detector).plan(text,ev)
                self.assertEqual(plan.mask_intervals,tuple((m['start'],m['end']) for m in original['mask_intervals']))
                new=prepare(text,plan,input_kind='document',hmac_key=key,key_tag=key_tag(key))
                old=pseudonymize_local(text,original,hmac_key=key).sanitized_text
                self.assertEqual(new.sanitized_text,old)
                self.assertEqual(v6_prepare('synthetic',text,detector,key).sanitized_text,old)
