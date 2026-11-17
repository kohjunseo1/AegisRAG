"""Isolated test-DB bootstrap must reject production targets before DB access."""
import unittest
from scripts.bootstrap_blind_db import verify_test_target,CONFIRM

class BlindBootstrapGuards(unittest.TestCase):
    def test_accepts_only_exact_local_test_database(self):
        good={'dbname':'aegis_blind_test','host':'127.0.0.1','port':5544}
        verify_test_target(good,CONFIRM)
        for bad in (
            dict(good,dbname='pii_rag'),
            dict(good,port=5433),
            dict(good,host='remote.example'),
            dict(good,dbname='aegis_blind_test_copy'),
        ):
            with self.assertRaisesRegex(ValueError,'REFUSE_NON_ISOLATED_TEST_DB'):
                verify_test_target(bad,CONFIRM)
        with self.assertRaisesRegex(ValueError,'REFUSE_NON_ISOLATED_TEST_DB'):
            verify_test_target(good,'YES')
