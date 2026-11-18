import concurrent.futures
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unified_v1.contracts import Candidate, ContractError, Config, PROFILES, fingerprint
from unified_v1.adapters import (AxAdapter, VeilAdapter, KlueAdapter, KoElectraAdapter, DetectionEngine,
                                 CandidateReplay, decode_window, map_label, validate_assets)

class AdapterTests(unittest.TestCase):
    def test_profiles_missing_fail_closed(self):
        for profile in PROFILES:
            ev = DetectionEngine(profile, {}).detect('합성', input_kind='document')
            self.assertFalse(ev.ready)
            self.assertEqual(set(ev.model_status_by_source), set(PROFILES[profile]))
            self.assertEqual(set(ev.model_status_by_source.values()), {'UNAVAILABLE'})

    def test_four_adapters_and_concurrency(self):
        adapters = {a.source_id:a for a in [cls('mock', lambda t: [dict(label='person',start=1,end=len(t))])
                                          for cls in [AxAdapter,VeilAdapter,KlueAdapter,KoElectraAdapter]]}
        engine = DetectionEngine('quality4', adapters)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            evidence = list(pool.map(lambda t:engine.detect(t, input_kind='query'), ['😀금 재원','😀선우 님']))
        for t,e in zip(['😀금 재원','😀선우 님'],evidence):
            self.assertTrue(e.ready)
            for rows in e.raw_candidates_by_source.values():
                self.assertEqual(rows[0].validate(t).end, len(t))
        self.assertNotEqual(evidence[0].request_id,evidence[1].request_id)
        self.assertNotEqual(evidence[0].original_input_fingerprint,evidence[1].original_input_fingerprint)

    def test_errors_do_not_leak_or_fallback(self):
        def broken(t): raise RuntimeError(t)
        ev = DetectionEngine('own2',{'ax':AxAdapter('r', broken), 'veil':VeilAdapter('r',lambda t:[])}).detect('private',input_kind='query')
        self.assertFalse(ev.ready)
        self.assertEqual(ev.model_status_by_source, {'ax':'ERROR','veil':'OK'})
        self.assertNotIn('private',repr(ev))

    def test_invalid_candidates(self):
        for kw in [dict(start=-1),dict(end=99),dict(label='unknown'),dict(offset_unit='byte'),dict(original_input_fingerprint='bad')]:
            values=dict(source_id='ax',source_revision='r',label='person',start=0,end=1,original_input_fingerprint=fingerprint('😀가'))
            values.update(kw)
            with self.assertRaises(ContractError):Candidate(**values).validate('😀가')

    def test_bio_unicode_and_long_windows(self):
        text='😀금 재원\n선우 님' + '가'*1200
        spans=decode_window(text,[(1,2),(3,5),(6,8)],['B-NAME','I-NAME','S-PERSON'])
        self.assertEqual([(x['start'],x['end']) for x in spans],[(1,5),(6,8)])
        tail=decode_window(text,[(len(text)-1,len(text))],['B-person'],window_id='last')
        self.assertEqual(tail[0]['end'],len(text))
        with self.assertRaises(ContractError):map_label('B-unknown')
        self.assertIsNone(map_label('B-ORGANIZATION',('ORGANIZATION',)))
        with self.assertRaises(ContractError):decode_window(text,[(0,1)],[])

    def test_cache_strict_parity_no_rstrip(self):
        cache=CandidateReplay();t='금 재원 '
        c=Candidate('klue','r','person',0,4,fingerprint(t))
        cache.put(t,'document','klue','r','cfg',[c])
        self.assertEqual(cache.get(t,'document','klue','r','cfg'),(c,))
        for args in [(t.rstrip(),'document','klue','r','cfg'),(t,'query','klue','r','cfg'),(t,'document','klue','other','cfg'),(t,'document','klue','r','other')]:
            with self.assertRaisesRegex(ContractError,'CACHE_MISS'):cache.get(*args)

    def test_asset_validation(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'model';p.mkdir()
            hashes={}
            for name in ['config.json','tokenizer.json','tokenizer_config.json','model.safetensors']:
                (p/name).write_bytes(b'synthetic');hashes[name]=hashlib.sha256(b'synthetic').hexdigest()
            self.assertEqual(validate_assets(p,root,hashes),p)
            (p/'tokenizer.json').write_bytes(b'different')
            with self.assertRaisesRegex(ContractError,'MODEL_ARTIFACT_MISMATCH'):validate_assets(p,root,hashes)
            with self.assertRaises(ContractError):validate_assets('relative',root,hashes)
            with self.assertRaisesRegex(ContractError,'MODEL_MISSING'):validate_assets(Path(root)/'absent',root,hashes)

    def test_config_rejects_unsafe_flags(self):
        for kw in [dict(api_live=True),dict(index_mask_only=True),dict(mode='approved_hcx'),dict(hmac_bits=32),dict(detector_profile='adaptive')]:
            with self.assertRaises(ContractError):Config(**kw)
