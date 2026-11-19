import ast
from pathlib import Path
import unittest
from unified_v1.contracts import ContractError
from unified_v1.lean_policy import person_boundaries,typed_plan,materialize,checked

KEY=b'lean-synthetic-only-key-32-bytes!!'
def span(label,a,b):return dict(label=label,start=a,end=b)

class LeanTests(unittest.TestCase):
    def test_ambiguous_name_i_kept_without_independent_context(self):
        for name in ['백송이','남궁수이','박준','김이','하늘']:
            text='성명: '+name
            self.assertEqual(person_boundaries(text,[span('person',4,len(text))])[0]['end'],len(text))
    def test_title_preserves_real_name_i(self):
        text='백송이 대리와 백송이 님'
        self.assertEqual(person_boundaries(text,[span('person',0,3)])[0]['end'],3)
    def test_document_internal_clear_short_name_can_correct_i(self):
        text='안윤님, 안윤이 처리합니다.'
        self.assertEqual(person_boundaries(text,[span('person',5,8)])[0]['end'],7)
    def test_no_unconfirmed_hmac_from_surname_or_plain_name(self):
        for text in ['백송이','황보수','이','하늘']:
            p=typed_plan(text,[span('person',0,len(text))],[span('person',0,len(text))])
            self.assertEqual(p['state'],'REVIEW_REQUIRED');self.assertEqual(p['hmac'],[])
            with self.assertRaises(ContractError):materialize(text,p,KEY)
    def test_explicit_field_confirms_common_noun_like_name(self):
        text='성명: 하늘';sp=[span('person',4,6)];p=typed_plan(text,sp,sp)
        self.assertEqual(p['state'],'LOCAL_PREPARED')
        result=materialize(text,p,KEY)
        self.assertNotIn('하늘',result.sanitized_text)
        self.assertRegex(result.sanitized_text,r'<PERSON_[0-9a-f]{24}>')
    def test_mask_union_preserves_discarded_raw_span_without_auto_approval(self):
        text='성명: 백송이';sp=[span('person',4,6)];p=typed_plan(text,sp,[span('person',4,7)])
        self.assertEqual(p['masks'],((4,7),));self.assertEqual(p['state'],'REVIEW_REQUIRED')
        self.assertNotIn('백송이',materialize(text,p,KEY,preview=True).sanitized_text)
    def test_13_digits_do_not_confirm_rrn_without_field(self):
        text='123456-1234567';sp=[span('resident_registration_number',0,len(text))]
        p=typed_plan(text,sp,sp);self.assertEqual(p['hmac'],[])
    def test_conflicting_types_abstain_and_keep_mask(self):
        text='성명: 하늘';sp=[span('person',4,6),span('address',4,6)]
        p=typed_plan(text,sp,sp);self.assertEqual(p['hmac'],[]);self.assertEqual(p['masks'],((4,6),))
    def test_invalid_input_is_fail_closed(self):
        for sp in [span('person',0,99),span('extra_sensitive:ALIEN_ID',0,1),span('person',False,1)]:
            with self.assertRaises(ContractError):checked('합성',[sp])
    def test_no_ensemble_is_distinct_from_no_koelectra(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from unified_v1.lean_worker import configure_pipeline,finish_team
        pipeline=SimpleNamespace();configure_pipeline(pipeline)
        self.assertFalse(pipeline.ENSEMBLE);self.assertFalse(pipeline.HYBRID)
        hybrid=Mock();hybrid.post_filter.return_value=([],[])
        auxiliary=Mock(return_value=[span('person',0,2)])
        finish_team('C1','합성',[],hybrid,auxiliary)
        auxiliary.assert_not_called();hybrid.combine.assert_not_called()
        hybrid.combine.return_value=([span('person',0,2)],[])
        finish_team('B1','합성',[],hybrid,auxiliary)
        auxiliary.assert_called_once_with('합성');hybrid.combine.assert_called_once()

if __name__=='__main__':unittest.main()
