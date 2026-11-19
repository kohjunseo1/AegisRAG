from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from unified_v1.generation import ContextBuilder,LivePermit,LiveTransport,GuardedGenerator,MockGenerator,MockTransport
from unified_v1.contracts import ContractError
from test_p5 import setup_context

class LocalVersusExternalTests(unittest.TestCase):
    def test_legacy_only_local_mock_and_live_never_inherits_permission(self):
        gen,q,rows=setup_context()
        legacy=replace(rows[0],approval_state='LEGACY_READ_ONLY',index_version='legacy_v6_readonly_koe5')
        builder=ContextBuilder(gen.builder.guard,key_tag=gen.builder.key_tag,policy_version=gen.builder.policy_version,
                               index_version=legacy.index_version,corpus_id=gen.builder.corpus_id,local_read_only=True)
        with patch('urllib.request.urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            self.assertEqual(MockGenerator(builder,gen.transport).generate(q,(legacy,)).decision,'ANSWER')
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'.env';env.write_text('')
            live=GuardedGenerator(builder,LiveTransport(env,gen.builder.guard))
            with patch('hcx.call_hcx') as api:
                with self.assertRaisesRegex(ContractError,'CANNOT_GO_LIVE'):live.generate(q,(legacy,))
                api.assert_not_called()
        with self.assertRaises(ContractError):LivePermit('a'*64,3,10,1,True).validate()

    def test_public_redaction_controls_are_not_ner_inputs_but_nearby_pii_is_checked(self):
        from test_p5 import guard
        seen=[]
        def detector(text):
            seen.append(text)
            if 'REDACTED' in text:return [dict(label='person',start=1,end=9)]
            if '금재원' in text:
                start=text.index('금재원');return [dict(label='person',start=start,end=start+3)]
            return []
        check=guard(detector)
        self.assertTrue(check.check('[REDACTED_UNCLASSIFIED] [ROLE_REFERENCE] 안내'))
        self.assertNotIn('REDACTED',seen[-1])
        self.assertEqual(len(seen[-1]),len('[REDACTED_UNCLASSIFIED] [ROLE_REFERENCE] 안내'))
        with self.assertRaises(ContractError):check.check('[REDACTED_UNCLASSIFIED] 금재원')
        with self.assertRaises(ContractError):check.check('[REDACTED_EMAIL] fixture@example.invalid')
