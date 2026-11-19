from unittest.mock import patch
import unittest
from unified_v1.experiments import synthetic_hcx_live
from unified_v1.__main__ import main

class ExperimentCliTests(unittest.TestCase):
    def test_live_default_off_does_not_open_workers_or_transport(self):
        with patch('hcx.call_hcx') as api,patch('unified_v1.experiments.ModelSession') as models:
            result=synthetic_hcx_live(None,None)
            self.assertEqual(result['status'],'NOT_RUN_BY_POLICY')
            api.assert_not_called();models.assert_not_called()

    def test_live_private_input_flag_rejected_without_call(self):
        with patch('sys.argv',['unified_v1','--synthetic-hcx-live','--documents','private.jsonl']),patch('hcx.call_hcx') as api:
            with self.assertRaises(SystemExit):main()
            api.assert_not_called()
