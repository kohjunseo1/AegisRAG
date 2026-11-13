"""Ensure organizational HF repository IDs keep the frozen release revisions."""
import json
from pathlib import Path
import unittest

MANIFEST = Path(__file__).resolve().parents[1] / 'src/pii_detector/model_manifest.json'

class TestOrgManifest(unittest.TestCase):
    def test_org_ids_preserve_frozen_revisions(self):
        obj = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual(obj['ax']['repo'], 'PII-detect/pii-ax-mixed-v1')
        self.assertEqual(obj['veil']['repo'], 'PII-detect/pii-veil-mixed-v1')
        self.assertEqual(obj['ax']['revision'], 'c0f99b1dc113befefeff1a3091087d50bc3ea635')
        self.assertEqual(obj['veil']['revision'], 'e6e7d20fa9baa43a938f5f6cc780d3f2c9650fb9')

if __name__ == '__main__':
    unittest.main()
