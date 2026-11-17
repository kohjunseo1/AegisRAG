"""Offline Holdout preflight regression. No models, DB, HCX or private fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_holdout_e2e as preflight


class HoldoutE2EPreflightContracts(unittest.TestCase):
    def setUp(self):
        self.docs = [
            {'id': 'H0001', 'text': '가상 고객의 교육 신청 문서'},
            {'id': 'H0002', 'text': '가상 고객의 안전교육 안내'},
        ]
        self.queries = [
            {'qid': 'Q001', 'query': '가상 고객 교육 문서를 찾아줘'},
            {'qid': 'Q002', 'query': '안전교육은 언제 진행돼?'},
        ]
        self.gold = [
            {'qid': 'Q001', 'relevant_doc_ids': ['H0001']},
            {'qid': 'Q002', 'relevant_doc_ids': ['H0002']},
        ]

    def build(self):
        return preflight.build_cases(
            self.docs, self.queries, self.gold, expected_docs=2, expected_queries=2)

    def test_exact_cross_file_join_and_case_order(self):
        self.gold.reverse()
        cases, info = self.build()
        self.assertEqual([c['case_id'] for c in cases], ['Q001', 'Q002'])
        self.assertEqual(cases[0]['relevant_doc_ids'], ['H0001'])
        self.assertEqual(cases[1]['relevant_doc_ids'], ['H0002'])
        self.assertEqual(info['documents'], 2)
        self.assertTrue(info['query_gold_id_match'])
        self.assertNotIn('expected_decision', cases[0])

    def test_missing_and_duplicate_query_gold_are_rejected(self):
        self.gold[1]['qid'] = 'Q003'
        with self.assertRaisesRegex(preflight.PreflightError, 'HOLDOUT_QUERY_GOLD_MISMATCH'):
            self.build()
        self.gold[1]['qid'] = 'Q001'
        with self.assertRaisesRegex(preflight.PreflightError, 'HOLDOUT_DUPLICATE_IDS'):
            self.build()

    def test_outside_gold_document_is_rejected(self):
        self.gold[0]['relevant_doc_ids'] = ['H9999']
        with self.assertRaisesRegex(preflight.PreflightError, 'HOLDOUT_GOLD_OUTSIDE_CORPUS'):
            self.build()

    def test_count_mismatch_is_rejected(self):
        with self.assertRaisesRegex(preflight.PreflightError, 'HOLDOUT_EXPECTED_COUNTS_MISMATCH'):
            preflight.build_cases(self.docs, self.queries, self.gold)

    def test_private_output_no_overwrite_and_no_stdout_raw_text(self):
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            dest = base / 'e2e' / 'holdout_cases.jsonl'
            cases, _ = self.build()
            with patch.object(preflight, 'PRIVATE_ROOT', base):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    preflight.write_private_cases(dest, cases)
                self.assertEqual(out.getvalue(), '')
                self.assertTrue(dest.exists())
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
                data = [json.loads(line) for line in dest.read_text().splitlines()]
                self.assertEqual(data[0]['question'], self.queries[0]['query'])
                with self.assertRaisesRegex(preflight.PreflightError, 'HOLDOUT_OUTPUT_EXISTS_NO_OVERWRITE'):
                    preflight.write_private_cases(dest, cases)

    def test_output_outside_private_root_is_rejected(self):
        cases, _ = self.build()
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            with patch.object(preflight, 'PRIVATE_ROOT', base / 'private'):
                with self.assertRaisesRegex(preflight.PreflightError,
                                            'HOLDOUT_OUTPUT_MUST_BE_PRIVATE_JSONL'):
                    preflight.write_private_cases(base/'public.jsonl', cases)

    def test_cli_reports_counts_without_exposing_private_question(self):
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            secret = '가상개인정보 010-9999-0000'
            self.queries[0]['query'] = secret
            for filename, data in [
                ('docs.jsonl', self.docs), ('queries.jsonl', self.queries), ('gold.jsonl', self.gold)
            ]:
                (base/filename).write_text(
                    ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in data),
                    encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = preflight.main([
                    '--documents', str(base/'docs.jsonl'),
                    '--queries', str(base/'queries.jsonl'),
                    '--gold', str(base/'gold.jsonl'),
                    '--expected-documents', '2', '--expected-queries', '2',
                    '--check-only'
                ])
            self.assertEqual(code, 0)
            self.assertIn('HOLDOUT_E2E_PREFLIGHT', output.getvalue())
            self.assertNotIn(secret, output.getvalue())
            self.assertIn('"private_cases_written": false', output.getvalue())


if __name__ == '__main__':
    unittest.main()
