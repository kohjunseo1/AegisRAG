"""CPU-only unit checks for V7 data preparation (no HF, torch, model or uploads)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ax_v7_make_data as maker
import ax_v7_train as tr


def test_bootstrap_offsets_and_disjoint_templates(tmp_path):
    train=maker.generate(123,100,"train")
    dev=maker.generate(124,30,"dev")
    a=tmp_path/"train.jsonl"
    b=tmp_path/"dev.jsonl"
    for path,rows in ((a,train),(b,dev)):
        path.write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in rows),encoding="utf8")
    da=tr.load_rows(a,role="train")
    db=tr.load_rows(b,role="dev")
    tr.check_splits({"train":da,"dev":db})
    for row in train+dev:
        for ent in row["entities"]:
            assert 0<=ent["start"]<ent["end"]<=len(row["text"])
            assert ent["annotation_scope"] if "annotation_scope" in ent else row["annotation_scope"]=="seven_only"


def test_golds_cannot_overlap(tmp_path):
    p=tmp_path/"train.jsonl"
    row={"id":"1","text":"가나다라마바사","annotation_scope":"seven_only",
         "entities":[{"label":"person","start":1,"end":4},
                     {"label":"address","start":3,"end":6}]}
    p.write_text(json.dumps(row,ensure_ascii=False)+"\n",encoding="utf8")
    with pytest.raises(ValueError,match="overlapping"):
        tr.load_rows(p,role="train")


def test_test_data_blocked_from_training(tmp_path):
    p=tmp_path/"official_test.jsonl"
    p.write_text(json.dumps({"id":"t","text":"가나다","entities":[],
                             "annotation_scope":"seven_only"},ensure_ascii=False)+"\n")
    with pytest.raises(ValueError,match="withheld"):
        tr.load_rows(p,role="train")


def test_previous_model_selection_dev_blocked(tmp_path):
    p=tmp_path/"generated_val.jsonl"
    p.write_text(json.dumps({"id":"v","text":"가나다","entities":[],
                             "annotation_scope":"seven_only"},ensure_ascii=False)+"\n")
    with pytest.raises(ValueError,match="DEV_LEAKAGE"):
        tr.load_rows(p,role="dev")


def test_label_head_37_preserved(tmp_path):
    # One O + B/I for 18 entity types, seven target classes mapping one-to-one.
    types=["PERSON","PHONE","EMAIL","ADDRESS","RRN","ACCOUNT","CARD"]
    types.extend(f"OTHER_{i}" for i in range(11))
    labs=["O"]+[f"{part}-{lab}" for lab in types for part in ("B","I")]
    folder=tmp_path/"best_model"
    folder.mkdir()
    (folder/"config.json").write_text(json.dumps(
        {"id2label":{str(i):l for i,l in enumerate(labs)}},ensure_ascii=False))
    idx,labels,names,o=tr.read_labels(folder)
    assert len(idx)==37 and len(names)==7 and o==0
    class Stub:
        def __call__(self,text,**kw):
            # three 1-char tokens plus CLS/SEP with offset=(0,0)
            assert text=="가나다"
            return {"input_ids":[10,11,12,13,14],
                    "attention_mask":[1]*5,
                    "offset_mapping":[(0,0),(0,1),(1,2),(2,3),(0,0)]}
    row={"id":"x","text":"가나다","annotation_scope":"seven_only",
         "entities":[{"label":"person","start":0,"end":2}]}
    x=tr.create_alignment([row],Stub(),idx,labels,names,o,32)[0]
    assert x["labels"]==[-100,names["person"][0],names["person"][1],-100,-100]
