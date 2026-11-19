"""Exercise the exact file-invocation import layout without local model weights."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ax_v7_review_cases.py"
SAFE_OUT = ROOT / "step2_privacy_rag" / "local_eval" / "ax_v7" / "unused_reader_check_only.jsonl"


class ReviewCasesStandaloneImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.jsonl"
        self.text = "문서 담당자 가영에게 확인했습니다."
        start = self.text.index("가영")

        self.row = {
            "id": "synthetic-one",
            "text": self.text,
            "entities": [
                {"label": "person", "start": start,
                 "end": start + 2, "text": "가영"}
            ],
        }

    def run_loader(self):
        # Match: PYTHONPATH=step2_privacy_rag/app python scripts/ax_v7_review_cases.py
        # cwd elsewhere confirms the repository root is NOT on sys.path.
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "step2_privacy_rag" / "app")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--data", str(self.path),
             "--out", str(SAFE_OUT), "--check-only"],
            env=env, cwd=self.tmp.name, capture_output=True, text=True,
            timeout=25, check=False,
        )

    def test_file_invocation_uses_strict_reader(self):
        self.path.write_text(json.dumps(self.row, ensure_ascii=False)+"\n",
                             encoding="utf-8")
        result = self.run_loader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AX_V7_REVIEW_INPUT_OK documents=1", result.stdout)
        self.assertFalse(SAFE_OUT.exists())

    def test_file_invocation_rejects_bad_offsets(self):
        bad = dict(self.row)
        bad["entities"] = [dict(self.row["entities"][0], end=3)]
        self.path.write_text(json.dumps(bad, ensure_ascii=False)+"\n",
                             encoding="utf-8")
        result = self.run_loader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLIND_GOLD_OFFSET_MISMATCH", result.stderr)
        self.assertNotIn("No module named 'scripts'", result.stderr)


if __name__ == "__main__":
    unittest.main()
