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


    def test_verified_o_token_supervision_not_whole_document(self):
        train = maker.generate(234, 100, "train")
        negatives = [x for x in train if x.get("verified_o_spans")]
        self.assertTrue(negatives)
        row = negatives[0]
        r = row["verified_o_spans"][0]
        self.assertEqual(row["text"][r["start"]:r["end"]], r["text"])
        path = self.dump("train.jsonl", [row])
        tr.load_rows(path, role="train")
        # Unknown tokens outside the explicit O interval must stay -100.
        types = ["PERSON","PHONE","EMAIL","ADDRESS","RRN","ACCOUNT","CARD"]
        types.extend(f"OTHER_{i}" for i in range(11))
        tagset = ["O"] + [f"{prefix}-{t}" for t in types for prefix in ("B","I")]
        folder = self.folder / "negative_model"
        folder.mkdir()
        (folder/"config.json").write_text(json.dumps({
            "id2label": {str(i): tag for i,tag in enumerate(tagset)}
        }), encoding="utf-8")
        idx, labels, names, o_id = tr.read_labels(folder)
        class CharTokenizer:
            def __call__(self, text, **kw):
                return {
                    "input_ids": list(range(1, len(text)+3)),
                    "attention_mask": [1]*(len(text)+2),
                    "offset_mapping": [(0,0)]+[(i,i+1) for i in range(len(text))]+[(0,0)],
                }
        labels_out = tr.create_alignment([row], CharTokenizer(),
                                          idx, labels, names, o_id, 512)[0]["labels"]
        for i in range(len(row["text"])):
            self.assertEqual(labels_out[i+1], o_id if r["start"] <= i < r["end"] else -100)

    def test_verified_o_cannot_overlap_gold(self):
        row={"id":"x", "text":"고객 김민수에게", "annotation_scope":"seven_only",
             "entities":[{"label":"person","start":3,"end":6}],
             "verified_o_spans":[{"start":4,"end":5,"text":"민"}]}
        with self.assertRaisesRegex(ValueError, "verified O overlaps"):
            tr.load_rows(self.dump("train.jsonl", [row]),role="train")

    def test_positive_token_straddling_boundary_is_rejected(self):
        row={"id":"x", "text":"가나다", "annotation_scope":"seven_only",
             "entities":[{"label":"person","start":0,"end":2}]}
        types=["PERSON","PHONE","EMAIL","ADDRESS","RRN","ACCOUNT","CARD"]
        types.extend(f"OTHER_{i}" for i in range(11))
        ids=["O"]+[f"{tag}-{typ}" for typ in types for tag in ("B","I")]
        folder = self.folder / "boundary_model"
        folder.mkdir()
        (folder/"config.json").write_text(json.dumps({
            "id2label":{str(i):v for i,v in enumerate(ids)}
        }),encoding="utf-8")
        idx,labels,names,o_id = tr.read_labels(folder)
        class CombinedTokenizer:
            def __call__(self, text, **kw):
                return {"input_ids":[0,1,2],"attention_mask":[1,1,1],
                        "offset_mapping":[(0,0),(0,3),(0,0)]}
        with self.assertRaisesRegex(ValueError, "ENTITY_TOKEN_BOUNDARY_MISMATCH"):
            tr.create_alignment([row], CombinedTokenizer(), idx, labels,
                                names, o_id, 384)


if __name__=="__main__":
    unittest.main()
