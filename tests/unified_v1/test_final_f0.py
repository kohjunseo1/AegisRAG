import unittest
from unified_v1.adapters import koelectra_label,decode_koelectra_window
from unified_v1.contracts import ContractError,Candidate,fingerprint
from unified_v1.runtime import profile_status
from unified_v1.protection import fuse,prepare,covers
from test_p2 import evidence,baseline,KEY,TAG

class FourModelTests(unittest.TestCase):
    def test_explicit_29_type_policy_no_alien_rrn_alias(self):
        self.assertEqual(koelectra_label('B-NAME'),'person')
        self.assertEqual(koelectra_label('B-ZIPCODE'),'address')
        self.assertEqual(koelectra_label('B-ALIEN_ID'),'extra_sensitive:ALIEN_ID')
        for kind in ['PASSPORT','CVC','IP_ADDRESS','USER_ID','SECRET']:self.assertTrue(koelectra_label('B-'+kind).startswith('extra_sensitive:'))
        self.assertIsNone(koelectra_label('B-DATE'))
        with self.assertRaises(ContractError):koelectra_label('B-UNKNOWN')
        self.assertEqual(profile_status('quality4',{s:'LOCAL_MODEL_PASS' for s in ['ax','veil','klue','koelectra']}),'REAL_READY')

    def test_extra_sensitive_is_protected_review_never_hmac(self):
        from dataclasses import replace
        text='합성 비밀 1234';ev=evidence(text)
        c=Candidate('ax','mock','extra_sensitive:CVC',6,10,fingerprint(text),evidence_codes=('EXTRA_SENSITIVE_CANDIDATE',))
        ev=replace(ev,raw_candidates_by_source={'ax':(c,),'veil':()})
        p=fuse(text,ev,baseline(),policy='evidence_v2')
        self.assertTrue(covers((6,10),p.mask_intervals));self.assertFalse(p.entities_for_hmac)
        self.assertEqual(prepare(text,p,input_kind='document',hmac_key=KEY,key_tag=TAG).state,'REVIEW')

    def test_unicode_raw_decoder_distinguishes_address_postcode_and_extra(self):
        text='😀 선우 12345 999'
        rows=decode_koelectra_window(text,[(1,4),(5,10),(11,14)],['B-NAME','B-ZIPCODE','B-CVC'])
        self.assertEqual([(r['label'],r['start'],r['end']) for r in rows], [('person',2,4),('address',5,10),('extra_sensitive:CVC',11,14)])

    def test_resident_normal_close_runs_cleanup_but_faults_still_fail_closed(self):
        import tempfile,sys
        from pathlib import Path
        from unified_v1.adapters import PersistentPredictor
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'cleaned'
            script=Path(temp)/'worker.py'
            script.write_text('import sys,json,atexit\nfrom pathlib import Path\natexit.register(lambda:Path('+repr(str(marker))+').write_text("closed"))\nfor line in sys.stdin:\n r=json.loads(line);r.pop("text");r["candidates"]=[]\n print(json.dumps(r),flush=True)\n')
            worker=PersistentPredictor([str(Path(sys.executable).absolute()),str(script)],'cfg',timeout=2)
            with worker:self.assertEqual(worker('합성'),[])
            self.assertEqual(marker.read_text(),'closed')
            with self.assertRaisesRegex(ContractError,'WORKER_FAULTED'):worker('합성')
