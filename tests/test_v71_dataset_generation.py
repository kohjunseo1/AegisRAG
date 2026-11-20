"""Regression tests: V7.1 generator must complete and be re-readable.

Run: python -m unittest tests/test_v71_dataset_generation.py -v
The large test deliberately uses the original failing 3200/480 sizes.
"""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT / "scripts"
sys.path.insert(0,str(SCRIPTS))
import ax_v71_make_data as v71
import ax_v7_make_data as v7


class TestV71DatasetGeneration(unittest.TestCase):
    def test_no_exhaustion_prone_old_generator(self):
        with patch.object(v7, "generate", side_effect=AssertionError("Legacy generator must not be called")):
            rows=v71.generate(7193,200,"train")
        self.assertEqual(len(rows),200)
        self.assertTrue(any(r["case_family"]=="person_ko_spaced" for r in rows))
        self.assertTrue(any(r["case_family"]=="person_ko_short" for r in rows))

    def test_original_failing_sizes_generate_and_audit(self):
        with tempfile.TemporaryDirectory(prefix="aegis-v71-test-") as tmp:
            dest=Path(tmp)/"data"
            base=[sys.executable,str(SCRIPTS/"ax_v71_make_data.py"),"--out",str(dest)]
            for command in (base+["--train-n","3200","--dev-n","480"],
                            base+["--audit-only"]):
                run=subprocess.run(command,capture_output=True,text=True,timeout=45)
                self.assertEqual(run.returncode,0,f"stdout={run.stdout}\nstderr={run.stderr}")
                self.assertIn("V71_SYNTHETIC_AUDIT_PASS",run.stdout)
            train=[json.loads(x) for x in (dest/"train.jsonl").read_text(encoding="utf-8").splitlines()]
            dev=[json.loads(x) for x in (dest/"dev.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual((len(train),len(dev)),(3200,480))
            summary=v71.validate(train,dev)
            self.assertEqual(set(summary["train"]["labels"]),set(v71.TYPES))
            self.assertEqual(set(summary["dev"]["labels"]),set(v71.TYPES))
            self.assertGreater(summary["train"]["verified_O"],0)

    def test_missing_audit_files_have_clear_error(self):
        with tempfile.TemporaryDirectory(prefix="aegis-v71-absent-") as tmp:
            dest=Path(tmp)/"missing"
            run=subprocess.run([sys.executable,str(SCRIPTS/"ax_v71_make_data.py"),
                                "--out",str(dest),"--audit-only"],
                               capture_output=True,text=True,timeout=15)
            self.assertNotEqual(run.returncode,0)
            self.assertIn("DATASET_NOT_GENERATED",run.stderr)


if __name__=="__main__":
    unittest.main()
