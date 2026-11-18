import unittest
from unified_v1.protection import fuse,prepare,covers
from unified_v1.evaluation import compare_plans
from unified_v1.replay import SnapshotGateAdapter
from test_p2 import evidence,baseline,KEY,TAG

class FusionV2Tests(unittest.TestCase):
    def test_new_complete_name_is_local_mask_only_not_automatic_review_or_approval(self):
        text='선우 님 안내';ev=evidence(text)
        old=fuse(text,ev,baseline())
        new=fuse(text,ev,baseline(),policy='evidence_v2')
        self.assertEqual(old.protection_status,'REVIEW')
        self.assertEqual(new.protection_status,'LOCAL_MASK_ONLY_PENDING_APPROVAL')
        self.assertTrue(covers((0,2),new.mask_intervals))
        p=prepare(text,new,input_kind='document',hmac_key=KEY,key_tag=TAG)
        self.assertEqual((p.state,p.protection_kind,p.approval_state),('LOCAL_PREPARED','MASKED_ONLY','NOT_APPROVED'))
        result=compare_plans(old,new,[dict(label='person',start=0,end=2)])
        self.assertEqual(result['new']['character_protection_rate'],1)
        self.assertEqual(result['new']['hmac_confirmation_rate'],0)

    def test_valid_phone_confirmed_conflicts_stay_review_and_baseline_never_shrinks(self):
        text='전화 010-0000-0000';ev=evidence(text)
        plan=fuse(text,ev,baseline(masks=[dict(start=0,end=2)]),policy='evidence_v2')
        self.assertEqual(plan.protection_status,'CONFIRMED_HMAC')
        self.assertEqual(len(plan.entities_for_hmac),1)
        self.assertTrue(covers((0,2),plan.mask_intervals))
        raw=[dict(label='address',start=3,end=len(text))]
        p=fuse(text,evidence(text,raw),baseline(),policy='evidence_v2')
        self.assertEqual(p.protection_status,'REVIEW')
        self.assertFalse(p.entities_for_hmac)

    def test_unknown_profile_gate_blocks_and_common_words_do_not_become_person_hmac(self):
        for text in ['안전교육 안내','절차 검토 완료','담당자: 안내']:
            p=fuse(text,evidence(text),baseline(),policy='evidence_v2')
            self.assertFalse(p.entities_for_hmac)
            self.assertNotEqual(p.protection_status,'BLOCKED')
