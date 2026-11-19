"""CPU-only unittest checks for V7 preparation without HF, torch or credentials."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ax_v7_make_data as maker
import ax_v7_train as tr


class V7DataContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def dump(self, name, rows):
        path = self.folder / name
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        return path

    def test_bootstrap_offsets_and_disjoint_templates(self):
        train = maker.generate(123, 100, "train")
        dev = maker.generate(124, 30, "dev")
        da = tr.load_rows(self.dump("train.jsonl", train), role="train")
        db = tr.load_rows(self.dump("dev.jsonl", dev), role="dev")
        tr.check_splits({"train": da, "dev": db})
        for row in train + dev:
            self.assertEqual(row["annotation_scope"], "seven_only")
            for ent in row["entities"]:
                self.assertTrue(0 <= ent["start"] < ent["end"] <= len(row["text"]))

    def test_gold_overlap_rejected(self):
        row = {"id":"1","text":"가나다라마바사","annotation_scope":"seven_only",
               "entities":[{"label":"person","start":1,"end":4},
                           {"label":"address","start":3,"end":6}]}
        with self.assertRaisesRegex(ValueError, "overlapping"):
            tr.load_rows(self.dump("train.jsonl", [row]), role="train")

    def test_test_data_rejected_as_training(self):
        row={"id":"t","text":"가나다","entities":[],"annotation_scope":"seven_only"}
        with self.assertRaisesRegex(ValueError, "withheld"):
            tr.load_rows(self.dump("official_test.jsonl", [row]), role="train")

    def test_previous_val_rejected_as_fresh_dev(self):
        row={"id":"v","text":"가나다","entities":[],"annotation_scope":"seven_only"}
        with self.assertRaisesRegex(ValueError, "DEV_LEAKAGE"):
            tr.load_rows(self.dump("generated_val.jsonl", [row]), role="dev")

    def test_37_label_alignment_for_partial_data(self):
        types = ["PERSON","PHONE","EMAIL","ADDRESS","RRN","ACCOUNT","CARD"]
        types.extend(f"OTHER_{i}" for i in range(11))
        labs = ["O"] + [f"{part}-{typ}" for typ in types for part in ("B","I")]
        folder = self.folder / "best_model"
        folder.mkdir()
        (folder/"config.json").write_text(
            json.dumps({"id2label":{str(i): lab for i,lab in enumerate(labs)}}),
            encoding="utf-8",
        )
        idx,labels,names,o_id = tr.read_labels(folder)
        self.assertEqual(len(idx),37)
        self.assertEqual(len(names),7)
        self.assertEqual(o_id,0)

        class StubTokenizer:
            def __call__(self,text,**kwargs):
                if text!="가나다":
                    raise ValueError("unknown test text")
                return {"input_ids":[10,11,12,13,14],
                        "attention_mask":[1]*5,
                        "offset_mapping":[(0,0),(0,1),(1,2),(2,3),(0,0)]}

        row={"id":"x","text":"가나다","annotation_scope":"seven_only",
             "entities":[{"label":"person","start":0,"end":2}]}
        aligned=tr.create_alignment([row],StubTokenizer(),idx,labels,names,o_id,32)
        self.assertEqual(
            aligned[0]["labels"],
            [-100,names["person"][0],names["person"][1],-100,-100],
        )


if __name__=="__main__":
    unittest.main()
