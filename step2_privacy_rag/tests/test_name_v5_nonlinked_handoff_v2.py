"""Synthetic, local-only tests. No raw real PII, keys, network, index or DB."""
import unittest
from unittest.mock import patch

from name_v5_nonlinked_handoff_v2 import preview_one, summarize, _protection_plan
from name_mask_decision_v5 import decide
from name_boundary_policy_v3_1 import propose
from local_pseudonymizer_v1 import pseudonymize_local

KEY=b'test-key-for-local-only-contract-checks!'  # test-only, not secret

class Fake:
    def __init__(self, result, ax=(), veil=()):
        self.result=result
        self.ax=list(ax)
        self.veil=list(veil)
    def detect(self, _):return self.result
    def ax_predict(self, _):return self.ax
    def veil_predict(self, _):return self.veil


def response(text, names=(), other=(), status='READY_FOR_PSEUDONYMIZATION'):
    entities=[]
    for label, fragment in [('person', v) for v in names] + list(other):
        a=text.index(fragment)
        entities.append({'label':label,'start':a,'end':a+len(fragment)})
    return {'status':status,'entities':entities,
            'mask_intervals':[{'start':e['start'],'end':e['end']} for e in entities],
            'issues':[] if status=='READY_FOR_PSEUDONYMIZATION' else [{'code':'possible_uncovered_name_suffix'}]}


class TestNonlinkedHandoff(unittest.TestCase):
    def test_01_ambiguous_ready_now_local_only(self):
        t='고객 김민준이 조건을 신청했다'
        out=preview_one('F001',t,Fake(response(t,names=['김민준'])),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['nonlinked_name_mentions'],1)
        self.assertEqual(out['suppressed_person_hmac_entities'],1)
        self.assertEqual(out['extra_masked_chars'],1)

    def test_02_ambiguous_existing_full_name_is_redacted(self):
        t='고객 백송이 조건을 신청했다'
        out=preview_one('F002',t,Fake(response(t,names=['백송이'])),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['nonlinked_name_mentions'],1)
        self.assertEqual(out['suppressed_person_hmac_entities'],1)

    def test_03_mask_full_candidate_without_person_hmac(self):
        t='고객 김민준이 조건을 신청했다'
        r=response(t,names=['김민준'])
        from privacy_gate_v4 import check_privacy_gate
        from name_v5_nonlinked_handoff_v2 import _FrozenDetectorView
        gate=check_privacy_gate(_FrozenDetectorView(Fake(r),r),t)
        self.assertEqual(gate['decision'],'READY_FOR_LOCAL_PSEUDONYMIZATION')
        new,counts=_protection_plan(t,r,gate,decide(t,r,'v5_privacy_first'),propose(t,r))
        self.assertEqual(new['entities_for_pseudonymization'],[])
        out=pseudonymize_local(t,new,hmac_key=KEY)
        self.assertNotIn('김민준이',out.sanitized_text)
        self.assertIn('[REDACTED_UNCLASSIFIED]',out.sanitized_text)
        self.assertEqual(out.hmac_entity_count,0)
        self.assertEqual(out.vault_records,None)
        self.assertEqual(counts['nonlinked_name_mentions'],1)

    def test_04_review_stays_review(self):
        t='고객 김민준이 조건을 신청했다'
        x=preview_one('F004',t,Fake(response(t,names=['김민준'],status='REVIEW_REQUIRED')),key=KEY)
        self.assertEqual((x['state'],x['reason']),('REVIEW_QUEUE','STEP1_REVIEW'))

    def test_05_gate_review_kept(self):
        t='고객 김민준이 조건을 신청했다'
        with patch('privacy_gate_v4.check_privacy_gate',return_value={'decision':'REVIEW_REQUIRED'}):
            x=preview_one('F005',t,Fake(response(t,names=['김민준'])),key=KEY)
        self.assertEqual(x['reason'],'GATE_REVIEW')

    def test_06_gate_block_kept(self):
        t='고객 김민준이 조건을 신청했다'
        with patch('privacy_gate_v4.check_privacy_gate',return_value={'decision':'BLOCKED'}):
            x=preview_one('F006',t,Fake(response(t,names=['김민준'])),key=KEY)
        self.assertEqual((x['state'],x['reason']),('BLOCKED','GATE_BLOCKED'))

    def test_07_protection_insufficient_must_review(self):
        t='고객 김민준이 조건을 신청했다'; r=response(t,names=['김민준'])
        def broken_decision(_,__,___):
            return {'mask_intervals':[(t.index('김민준'),t.index('김민준')+3)],'added':set(),'would_abstain':False}
        with patch('name_v5_nonlinked_handoff_v2.decide',side_effect=broken_decision):
            x=preview_one('F007',t,Fake(r),key=KEY)
        self.assertEqual(x['reason'],'NAME_PROTECTION_INCOMPLETE')
        self.assertEqual(x['state'],'REVIEW_QUEUE')

    def test_08_collision_with_other_pii_review(self):
        t='고객 김민준이 조건을 신청했다'
        r=response(t,names=['김민준'],other=[('card_number','이')])
        x=preview_one('F008',t,Fake(r),key=KEY)
        self.assertEqual((x['state'],x['reason']),('REVIEW_QUEUE','NAME_SPAN_COLLISION'))

    def test_09_role_overlap_review(self):
        t='고객 김민준이 조건을 신청했다';r=response(t,names=['김민준'])
        from privacy_gate_v4 import check_privacy_gate
        from name_v5_nonlinked_handoff_v2 import _FrozenDetectorView
        g=check_privacy_gate(_FrozenDetectorView(Fake(r),r),t)
        g['redaction_intervals']=[{'start':t.index('김민준'),'end':t.index('김민준')+1}]
        with patch('privacy_gate_v4.check_privacy_gate',return_value=g):
            x=preview_one('F009',t,Fake(r),key=KEY)
        self.assertEqual(x['reason'],'NAME_SPAN_COLLISION')

    def test_10_unrelated_phone_hmac_survives(self):
        t='고객 김민준이 조건을 신청했다 전화 010-1234-5678'
        r=response(t,names=['김민준'],other=[('phone_number','010-1234-5678')])
        x=preview_one('F010',t,Fake(r),key=KEY)
        self.assertEqual(x['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(x['suppressed_person_hmac_entities'],1)
        from privacy_gate_v4 import check_privacy_gate
        from name_v5_nonlinked_handoff_v2 import _FrozenDetectorView
        g=check_privacy_gate(_FrozenDetectorView(Fake(r),r),t)
        new,_=_protection_plan(t,r,g,decide(t,r,'v5_privacy_first'),propose(t,r))
        out=pseudonymize_local(t,new,hmac_key=KEY)
        self.assertNotIn('김민준이',out.sanitized_text)
        self.assertEqual(out.hmac_entity_count,1)
        self.assertEqual(out.safe_token_manifest[0]['label'],'phone_number')

    def test_11_explicit_short_keeps_exact_gate_person_hmac(self):
        t='성명: 김민준\n김민준이 신청했다'
        a=t.index('김민준',t.index('\n'))
        r={'status':'READY_FOR_PSEUDONYMIZATION',
            'entities':[{'label':'person','start':a,'end':a+3}],
            'mask_intervals':[{'start':a,'end':a+3}], 'issues':[]}
        out=preview_one('F011',t,Fake(r),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['preserved_person_hmac_entities'],1)
        self.assertEqual(out['ambiguous_name_candidate_count'],0)

    def test_12_explicit_long_mismatch_suppresses_original_hmac(self):
        t='성명: 백송이\n백송이 신청했다'
        a=t.index('백송',t.index('\n'))
        r={'status':'READY_FOR_PSEUDONYMIZATION',
            'entities':[{'label':'person','start':a,'end':a+2}],
            'mask_intervals':[{'start':a,'end':a+2}], 'issues':[]}
        out=preview_one('F012',t,Fake(r),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['suppressed_person_hmac_entities'],1)

    def test_13_identity_conflict_is_nonlinked(self):
        t='성명: 백송\n고객명: 백송이\n백송이 신청했다'
        a=t.index('백송',t.rfind('\n'))
        r={'status':'READY_FOR_PSEUDONYMIZATION',
            'entities':[{'label':'person','start':a,'end':a+2}],
            'mask_intervals':[{'start':a,'end':a+2}], 'issues':[]}
        out=preview_one('F013',t,Fake(r),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['suppressed_person_hmac_entities'],1)
        self.assertGreaterEqual(out['nonlinked_name_mentions'],1)

    def test_14_absent_person_ent_is_not_falsely_claimed_safe(self):
        t='문맥을 바꾸는 이름 김민준이 존재'
        r=response(t,names=())
        x=preview_one('F014',t,Fake(r),key=KEY)
        self.assertEqual(x['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(x['name_candidate_count'],0)
        # Model miss risk still exists; no false claim of 100% privacy.

    def test_15_malformed_step1_entity_is_blocked(self):
        t='고객 김민준이'
        r={'status':'READY_FOR_PSEUDONYMIZATION',
           'entities':[{'label':'person','start':8,'end':20}], 'mask_intervals':[],'issues':[]}
        x=preview_one('F015',t,Fake(r),key=KEY)
        self.assertEqual(x['state'],'BLOCKED')

    def test_16_invalid_key_fail_closed(self):
        t='고객 김민준이 조건을 신청했다'
        x=preview_one('F016',t,Fake(response(t,names=['김민준'])),key=b'abc')
        self.assertEqual(x['reason'],'PSEUDONYMIZATION_FAILED')

    def test_17_blocked_step1_still_blocked(self):
        x=preview_one('F017','검사',Fake({'status':'BLOCKED'}),key=KEY)
        self.assertEqual((x['state'],x['reason']),('BLOCKED','STEP1_BLOCKED'))

    def test_18_invalid_id_rejected(self):
        with self.assertRaises(ValueError):
            preview_one('F\nraw','name',Fake({'status':'BLOCKED'}),key=KEY)

    def test_19_output_whitelist_only(self):
        t='고객 김민준이 조건을 신청했다'
        x=preview_one('F019',t,Fake(response(t,names=['김민준'])),key=KEY)
        self.assertNotIn('김민준',str(x))
        self.assertNotIn('신청했다',str(x))
        self.assertTrue(set(x).issubset({'doc_id','state','reason','name_candidate_count',
            'ambiguous_name_candidate_count','extra_masked_chars','nonlinked_name_mentions',
            'suppressed_person_hmac_entities','preserved_person_hmac_entities','gate_checked',
            'explicit_name_field_added_chars','unlinked_explicit_name_fields'}))

    def test_20_summary_not_index_approved(self):
        rows=[{'doc_id':'A','state':'LOCAL_DRYRUN_OK','reason':'LOCAL_ONLY_NOT_INDEX_AUTHORIZED'},
              {'doc_id':'B','state':'REVIEW_QUEUE','reason':'STEP1_REVIEW'}]
        z=summarize(rows)
        self.assertEqual(z['actual_index_writes'],0)
        self.assertEqual(z['automatic_review_to_ready_promotions'],0)
        self.assertEqual(z['state_counts']['REVIEW_QUEUE'],1)

    def test_21_clean_ready_still_ok(self):
        t='단순한 업무 문서'; x=preview_one('F021',t,Fake(response(t)),key=KEY)
        self.assertEqual(x['state'],'LOCAL_DRYRUN_OK')

    def test_22_no_cover_reduction_on_extra(self):
        t='고객 김민준이 조건을 신청했다';r=response(t,names=['김민준'])
        from privacy_gate_v4 import check_privacy_gate
        from name_v5_nonlinked_handoff_v2 import _FrozenDetectorView
        g=check_privacy_gate(_FrozenDetectorView(Fake(r),r),t)
        d=decide(t,r,'v5_privacy_first')
        revised,_=_protection_plan(t,r,g,d,propose(t,r))
        old=set().union(*(set(range(m['start'],m['end'])) for m in g['mask_intervals']))
        new=set().union(*(set(range(m['start'],m['end'])) for m in revised['mask_intervals']))
        self.assertTrue(old <= new)

    def test_23_gate_invalid_contract_blocks(self):
        t='고객 김민준이 조건을 신청했다';r=response(t,names=['김민준'])
        with patch('privacy_gate_v4.check_privacy_gate',return_value={
            'decision':'READY_FOR_LOCAL_PSEUDONYMIZATION',
            'mask_intervals':[], 'entities':[], 'entities_for_pseudonymization':'oops',
            'redaction_intervals':[]}):
            x=preview_one('F023',t,Fake(r),key=KEY)
        self.assertEqual(x['state'],'BLOCKED')

    def test_24_explicit_name_field_missed_by_ner_is_redacted(self):
        t='성명: 김민준\n신청 내역'
        r=response(t,names=())
        out=preview_one('F024',t,Fake(r),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['explicit_name_field_added_chars'],3)
        from privacy_gate_v4 import check_privacy_gate
        from name_v5_nonlinked_handoff_v2 import _FrozenDetectorView
        g=check_privacy_gate(_FrozenDetectorView(Fake(r),r),t)
        safe,_=_protection_plan(t,r,g,decide(t,r,'v5_privacy_first'),propose(t,r))
        rendered=pseudonymize_local(t,safe,hmac_key=KEY)
        self.assertNotIn('김민준',rendered.sanitized_text)
        self.assertEqual(rendered.hmac_entity_count,0)

    def test_25_partially_detected_name_field_is_unlinked(self):
        t='성명: 백송이\n접수 완료'
        r=response(t,names=['백송'])
        out=preview_one('F025',t,Fake(r),key=KEY)
        self.assertEqual(out['state'],'LOCAL_DRYRUN_OK')
        self.assertEqual(out['suppressed_person_hmac_entities'],1)
        self.assertEqual(out['explicit_name_field_added_chars'],0)  # V5 already covered terminal 이

    def test_26_name_field_overlapping_other_pii_goes_review(self):
        t='성명: 김민준'
        r=response(t,names=(),other=[('account_number','김민준')])
        out=preview_one('F026',t,Fake(r),key=KEY)
        self.assertEqual(out['reason'],'NAME_SPAN_COLLISION')

if __name__=='__main__':unittest.main()
