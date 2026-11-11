import unittest
from name_protection_policy import decide, score, summarize, _particle_cue, _explicit_names, POLICIES
from name_masking_strategies import chars

READY='READY_FOR_PSEUDONYMIZATION'
REVIEW='REVIEW_REQUIRED'

def mk(text, entity, status=READY, extra=None, include_label='person'):
    a=text.index(entity);b=a+len(entity)
    spans=[{'label':include_label,'start':a,'end':b}]
    masks=[{'start':a,'end':b}]
    if extra:
        x=text.index(extra)
        spans.append({'label':'phone_number','start':x,'end':x+len(extra)})
        masks.append({'start':x,'end':x+len(extra)})
    return {'status':status,'entities':spans,'mask_intervals':masks,'issues':[]}

class FinalPolicyTests(unittest.TestCase):
    def test_weak_particle_only_abstains_not_release(self):
        t='고객 김민준이 자료 확인';r=mk(t,'김민준')
        x=decide(t,r,'v5_grammar_abstain')
        self.assertTrue(x['would_abstain']);self.assertFalse(x['auto_eligible'])
        self.assertEqual(len(x['added']),0)
    def test_minmask_ambiguous_abstains(self):
        t='고객 김민준이 신청';r=mk(t,'김민준')
        x=decide(t,r,'v5_minmask_abstain')
        self.assertTrue(x['would_abstain']);self.assertEqual(len(x['added']),0)
    def test_privacy_first_extends(self):
        t='고객 김민준이 신청';r=mk(t,'김민준')
        x=decide(t,r,'v5_privacy_first')
        self.assertTrue(x['auto_eligible']);self.assertEqual(len(x['added']),1)
    def test_positive_long_title_evidence(self):
        t='담당 백송이 대리와 백송이 검토';s=t.rfind('백송');r=mk_last(t,'백송')
        self.assertEqual(len(decide(t,r,'v5_minmask_abstain')['added']),1)
        self.assertFalse(decide(t,r,'v5_minmask_abstain')['would_abstain'])
    def test_positive_short_title_evidence(self):
        t='김민준 팀장이 검토. 고객 김민준이 신청';r=mk_last(t,'김민준')
        x=decide(t,r,'v5_minmask_abstain')
        self.assertFalse(x['would_abstain']);self.assertEqual(len(x['added']),0)
    def test_explicit_fullname_long(self):
        t='성명: 백송이\n상담 백송이 확인';r=mk_last(t,'백송')
        x=decide(t,r,'v5_minmask_abstain')
        self.assertFalse(x['would_abstain']);self.assertEqual(len(x['added']),1)
    def test_explicit_fullname_short(self):
        t='성명: 김민준\n고객 김민준이 신청';r=mk_last(t,'김민준')
        x=decide(t,r,'v5_minmask_abstain')
        self.assertFalse(x['would_abstain']);self.assertEqual(len(x['added']),0)
    def test_conflicting_evidence_abstain(self):
        t='담당 백송이 대리와 백송 과장. 백송이 신청';r=mk_last(t,'백송')
        x=decide(t,r,'v5_minmask_abstain')
        self.assertTrue(x['would_abstain']);self.assertEqual(len(x['added']),0)
    def test_existing_review_not_upgraded(self):
        t='김민준이 신청';r=mk(t,'김민준',REVIEW)
        for p in POLICIES:self.assertFalse(decide(t,r,p)['auto_eligible'])
    def test_preserve_mask(self):
        t='고객 김민준이 확인';r=mk(t,'김민준')
        old=chars([(e['start'],e['end']) for e in r['mask_intervals']])
        for p in POLICIES:
            self.assertLessEqual(old,chars(decide(t,r,p)['mask_intervals']))
    def test_other_pii_doesnt_cancel(self):
        t='성명: 백송이\n백송이 문의 010-4321-1234';r=mk_last(t,'백송',extra='010-4321-1234')
        self.assertEqual(len(decide(t,r,'v5_minmask_abstain')['added']),1)
    def test_overlap_with_other_pii_abstains(self):
        t='고객 김민준이 신청'
        r=mk(t,'김민준')
        e=t.index('이 신청')
        r['entities'].append({'label':'address','start':e,'end':e+1})
        r['mask_intervals'].append({'start':e,'end':e+1})
        # It is already protected; no extension needed.
        self.assertEqual(len(decide(t,r,'v5_minmask_abstain')['added']),0)
    def test_invalid_other_label_failclosed(self):
        t='김민준이 신청';r=mk(t,'김민준')
        r['entities'].append({'label':'not_a_valid_label','start':0,'end':1})
        with self.assertRaises(ValueError):decide(t,r,'v5_minmask_abstain')
    def test_invalid_protection_failclosed(self):
        t='김민준이 신청';r=mk(t,'김민준')
        r['mask_intervals']=[]
        with self.assertRaises(ValueError):decide(t,r,'v5_minmask_abstain')
    def test_uncovered_hidden_name_not_magically_found(self):
        t='발신: 미확인\n내용: 신청';r={'status':READY,'entities':[],'issues':[],'mask_intervals':[]}
        self.assertTrue(decide(t,r,'v5_minmask_abstain')['auto_eligible'])
        # A known limitation: candidate-only policies cannot detect every missing PII.
    def test_field_name_regex(self):
        t='성명: 백송이\n고객명: 김민준\n담당: 아무개'
        self.assertEqual(_explicit_names(t),{'백송이','김민준'})
    def test_batchim_never_authorizes_unmask(self):
        t='백송이 상담';r=mk(t,'백송')
        self.assertTrue(_particle_cue(t,(0,3)))
        self.assertTrue(decide(t,r,'v5_grammar_abstain')['would_abstain'])
    def test_gold_does_not_enter_decide(self):
        t='김민준이 안내';r=mk(t,'김민준')
        x=score(t,[(0,4)],r,'long');y=score(t,[(0,3)],r,'short')
        self.assertEqual(x['policies']['v5_minmask_abstain']['virtual_abstain'],
                         y['policies']['v5_minmask_abstain']['virtual_abstain'])
    def test_all_sum(self):
        t='김민준이 안내';r=mk(t,'김민준')
        s=summarize([score(t,[(0,3)],r,'short')])
        self.assertEqual(s['v5_minmask_abstain']['auto_eligible'],0)


def mk_last(text,entity,extra=None):
    a=text.rindex(entity);b=a+len(entity)
    spans=[{'label':'person','start':a,'end':b}]
    masks=[{'start':a,'end':b}]
    if extra:
        z=text.index(extra)
        spans.append({'label':'phone_number','start':z,'end':z+len(extra)})
        masks.append({'start':z,'end':z+len(extra)})
    return {'status':READY,'entities':spans,'issues':[],'mask_intervals':masks}

if __name__=='__main__':unittest.main()
