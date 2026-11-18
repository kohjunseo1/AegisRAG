from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unified_v1.contracts import ContractError,config_hash
from unified_v1.generation import LivePermit,LiveTransport,GuardedGenerator
from test_p5 import setup_context,guard

class LiveCodeTests(unittest.TestCase):
    def test_default_off_and_existing_hcx_delegation_no_network(self):
        gen,q,rows=setup_context()
        context=gen.builder.build(q,rows)
        content=dict(question=context.question,documents=list(context.documents))
        permit=LivePermit(config_hash(content),max_calls=1,total_budget=1,reserved_cost_per_call=1,enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'.env';env.write_text('')
            off=GuardedGenerator(gen.builder,LiveTransport(env,guard()))
            with patch('hcx.call_hcx') as api:
                with self.assertRaises(ContractError):off.generate(q,rows)
                api.assert_not_called()
            transport=LiveTransport(env,guard(),permit=permit)
            live=GuardedGenerator(gen.builder,transport)
            response=dict(decision='ANSWER',answer='승인 절차를 거칩니다.',citations=['synthetic_doc'])
            with patch.dict('os.environ',{'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}),patch('hcx.call_hcx',return_value=(response,{},{})) as api,patch('urllib.request.urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')):
                result=live.generate(q,rows)
                self.assertEqual(result.citations,('synthetic_doc',))
                self.assertEqual(api.call_args.args[0],env.parent)
                self.assertEqual(api.call_count,1)
                self.assertEqual(transport.reserved_cost,Decimal('1'))
                with self.assertRaises(ContractError):live.generate(q,rows)
                self.assertEqual(api.call_count,1)

    def test_context_budget_private_corpus_and_output_recheck(self):
        gen,q,rows=setup_context()
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'.env';env.write_text('')
            permit=LivePermit('a'*64,max_calls=1,total_budget=1,reserved_cost_per_call=1,enabled=True)
            transport=LiveTransport(env,guard(),permit=permit)
            with patch.dict('os.environ',{'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}),patch('hcx.call_hcx') as api:
                with self.assertRaises(ContractError):GuardedGenerator(gen.builder,transport).generate(q,rows)
                api.assert_not_called()
            gen.builder.corpus_id='private_holdout'
            with self.assertRaisesRegex(ContractError,'PRIVATE_CORPUS'):GuardedGenerator(gen.builder,transport).generate(q,rows)
        for permit in [LivePermit('a'*64),LivePermit('a'*64,1,-1,1,True),LivePermit('a'*64,1,'NaN',1,True),LivePermit('a'*64,1,1,1,True,'holdout')]:
            with self.assertRaises(ContractError):permit.validate()

    def test_live_response_is_independently_rechecked_and_failed_attempt_consumes_allowance(self):
        from test_p4 import TOKEN2
        gen,q,rows=setup_context()
        ctx=gen.builder.build(q,rows);body=dict(question=ctx.question,documents=list(ctx.documents))
        with tempfile.TemporaryDirectory() as tmp:
            env=Path(tmp)/'.env';env.write_text('')
            for answer in [TOKEN2,'010-0000-0000']:
                permit=LivePermit(config_hash(body),1,1,1,True)
                transport=LiveTransport(env,guard(),permit=permit)
                with patch.dict('os.environ',{'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}),patch('hcx.call_hcx',return_value=(dict(decision='ANSWER',answer=answer,citations=['synthetic_doc']),{},{})) as api:
                    with self.assertRaises(ContractError):GuardedGenerator(gen.builder,transport).generate(q,rows)
                    self.assertEqual(transport.calls,1)
                    self.assertEqual(api.call_count,1)
            transport=LiveTransport(env,guard(),permit=LivePermit(config_hash(body),1,1,1,True))
            with patch.dict('os.environ',{'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED':'YES'}),patch('hcx.call_hcx',side_effect=TimeoutError('synthetic')) as api:
                for _ in range(2):
                    with self.assertRaises(ContractError):GuardedGenerator(gen.builder,transport).generate(q,rows)
                self.assertEqual(api.call_count,1)
