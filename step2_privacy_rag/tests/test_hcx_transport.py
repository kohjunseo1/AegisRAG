"""Synthetic HCX transport failures: errors must be actionable and never leak response bodies."""
import io
import json
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from hcx import BASE, OutboundError, call_hcx

DOCS = [{'doc_id': 'S001', 'text': '합성 교육은 수요일입니다.'}]

class HCXTransport(unittest.TestCase):
    def call_with(self, side_effect=None, response=None):
        env = {'AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED': 'YES',
               'LLM_API_KEY': 'test-key', 'LLM_API_BASE': BASE}
        with patch.dict('os.environ', env), patch('hcx.urllib.request.urlopen') as urlopen:
            if side_effect is not None:
                urlopen.side_effect = side_effect
            elif response is not None:
                urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
            result = call_hcx(Path('.'), '교육 일정?', DOCS, False)
            return result, urlopen.call_args

    def test_http_status_never_leaks_response_body(self):
        for status, expected in [(400, 'HCX_HTTP_400'), (401, 'HCX_HTTP_401'),
                                 (403, 'HCX_HTTP_403'), (429, 'HCX_HTTP_429'),
                                 (503, 'HCX_HTTP_5XX')]:
            with self.subTest(status=status):
                error = urllib.error.HTTPError(BASE, status, 'SECRET_RESPONSE', {}, io.BytesIO(b'PRIVATE_DOC_BODY'))
                with self.assertRaises(OutboundError) as caught:
                    self.call_with(side_effect=error)
                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertNotIn('SECRET', str(caught.exception))

    def test_network_error_is_safe(self):
        with self.assertRaisesRegex(OutboundError, '^HCX_CONNECTION_FAILED$'):
            self.call_with(side_effect=urllib.error.URLError('SECRET_DSN'))

    def test_timeout_is_safe(self):
        with self.assertRaisesRegex(OutboundError, '^HCX_TIMEOUT$'):
            self.call_with(side_effect=TimeoutError('PRIVATE_DOCUMENT'))

    def test_valid_response_contract(self):
        reply = dict(choices=[dict(finish_reason='stop', message=dict(
            content=json.dumps(dict(decision='ANSWER',answer='수요일입니다.',citations=['S001']))))],
            usage=dict(prompt_tokens=12,completion_tokens=8))
        (result, usage, _), called = self.call_with(response=reply)
        self.assertEqual(result['decision'], 'ANSWER')
        self.assertEqual(usage['prompt_tokens'], 12)
        payload = json.loads(called.args[0].data)
        self.assertEqual(payload['model'], 'HCX-007')
        self.assertEqual(payload['reasoning_effort'], 'none')
        self.assertEqual(payload['response_format']['type'], 'json_schema')

if __name__ == '__main__':
    unittest.main()
