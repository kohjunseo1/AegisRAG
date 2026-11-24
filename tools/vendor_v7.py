"""Vendor ONLY previously evaluated pure inference/policy code from the local V7 repo.

No Gold, cached predictions, weights, or evaluator modules are copied.
Run once; generated module is standalone. Refuse missing source definitions.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "starter/v7_frozen.py"
SPECS = (
    ("scripts/ax_v7_train.py", ("ALIASES", "load_fast_tokenizer")),
    ("scripts/ax_v7_eval.py", ("model_label", "decode")),
    ("scripts/v7_chunk_ablation_v2.py", ("window_bounds", "winning_ranges", "tokenizer_special_pair", "token_window_predict")),
    ("step1_pii_detector/src/pii_detector/detector.py", (
        "LABELS", "ADDRESS_HEAD", "PAREN_PREFIX", "BARE_PREFIX", "NON_POSTAL_FIELD",
        "POSTAL_FIELD", "ADDRESS_FIELD", "TRAILING_POSTAL", "ROLE_CUE", "NEXT_CONTEXT",
        "span_key", "overlaps", "normalize_spans", "validate_boundaries")),
    ("step1_pii_detector/src/pii_detector/structural_policy.py", (
        "TITLE", "SURNAME_TITLE_AFTER", "SURNAMES", "EXTENSION_VALUE", "_key", "_line",
        "_standalone_surname_title", "_explicit_internal_extension", "refine_spans", "mask_union")),
    ("scripts/v7_final_postpolicy_probe.py", ("validate_spans",)),
    ("scripts/v7_exit_gate.py", ("POSTAL_CODE", "POSTAL_GAP", "postal_merge")),
    ("scripts/v7_structural_fp_ablation.py", (
        "STATUS_EVENT", "STATUS_CONTEXT", "PERSON_FIELD", "PERSON_CONTINUATION",
        "MOURNING_PREFIX", "JOB_TITLE_BEFORE", "KINSHIP", "_before_after",
        "classify_nonperson", "apply_guard")),
)

def extract(path, names):
    raw = path.read_bytes()
    src = raw.decode("utf-8-sig")
    selected = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef):
            keys = [node.name]
        elif isinstance(node, ast.Assign):
            keys = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            keys = [node.target.id]
        else:
            continue
        for key in keys:
            if key in names:
                if key in selected:
                    raise ValueError("DUPLICATE_DEFINITION: " + key)
                selected[key] = ast.get_source_segment(src, node)
    missing = set(names) - selected.keys()
    if missing:
        raise ValueError(f"MISSING_DEFINITIONS {path.name}: {sorted(missing)}")
    return [selected[k] for k in names], hashlib.sha256(raw).hexdigest()

def replace_once(src, old, new):
    if src.count(old) != 1:
        raise ValueError("SOURCE_ANCHOR_MISMATCH")
    return src.replace(old, new, 1)

def build(source):
    pieces = []
    hashes = {}
    for rel, names in SPECS:
        values, hashes[rel] = extract(source / rel, names)
        if "postal_merge" in names:
            idx = names.index("postal_merge")
            code = values[idx]
            code = replace_once(code, "    from pii_detector.detector import ADDRESS_HEAD\n", "")
            code = code.replace("prev.validate_spans", "validate_spans")
            # Preserve the original join behavior except for an explicit non-postal field.
            # Also preserve offsets for later review; no original text is logged.
            code = replace_once(code,
                "        if left>0 and text[left-1].isalnum():continue\n",
                "        if left>0 and text[left-1].isalnum():continue\n"
                "        if NON_POSTAL_FIELD.search(text[max(0,left-35):left]):\n"
                "            events.append({'rule':'postal_join','status':'review_nonpostal_field',"
                "'start':left,'end':b})\n"
                "            continue\n")
            code = replace_once(code,
                "events.append({'rule':'postal_join','status':'merged'})",
                "events.append({'rule':'postal_join','status':'merged','start':left,'end':ad['end']})")
            values[idx] = code
        pieces.extend(["# Source: " + rel, *values])
    header = (
        '"""Generated from frozen V7 sources by tools/vendor_v7.py; no model or Gold data."""\n'
        "from __future__ import annotations\n"
        "import re\nimport json\nfrom pathlib import Path\nfrom typing import Iterable\n\n"
        "SOURCE_SHA256 = " + repr(hashes) + "\n"
    )
    return header + "\n\n".join(pieces) + "\n"

def self_test(m):
    def span(text, value, label):
        a = text.index(value)
        return {"label": label, "start": a, "end": a+len(value)}
    for n in (0, 1, 510, 511, 892, 893, 4096):
        w = m.window_bounds(n,510,128)
        owners = m.winning_ranges(w,n)
        assert sum(b-a for a,b in owners) == n
        assert [i for a,b in owners for i in range(a,b)] == list(range(n))
    t = "김 과장과 김민준 과장"
    raw = [span(t,"김","person"),span(t,"김민준","person")]
    out, ev = m.refine_spans(t,raw)
    assert out == raw[1:] and ev[0]["rule"] == "standalone_surname_title"
    assert m.mask_union(raw,out,t)[0] == {"start":0,"end":1}
    t = "내선번호: 1234"
    out, ev = m.refine_spans(t,[span(t,"1234","phone_number")])
    assert not out and ev[0]["rule"] == "explicit_internal_extension"
    t = "김민준이 신청함"
    p = span(t,"김민준이","person")
    out, ev = m.validate_boundaries(t,[p])
    assert out == [p] and any(e["status"]=="review_only" for e in ev)
    t = "(06236) 서울 강남구"
    p = span(t,"서울 강남구","address")
    out, ev = m.validate_boundaries(t,[p])
    assert out == [{"label":"address","start":0,"end":len(t)}]
    out, ev = m.postal_merge(t,[span(t,"06236","address"),p])
    assert out == [{"label":"address","start":0,"end":len(t)}]
    t = "고객번호: (12345) 서울 강남구"
    raw = [span(t,"12345","address"),span(t,"서울 강남구","address")]
    out, ev = m.postal_merge(t,raw)
    assert out == raw and any(e["status"]=="review_nonpostal_field" for e in ev)
    for text, value, remove in (
        ("결제 완료","완료",True),("고객명: 완료","완료",False),
        ("[부고] 김민준 과장 부친상","부친",True),
        ("김민준 과장","김민준",False)):
        p = span(text,value,"person")
        out, ev = m.apply_guard(text,[p])
        assert bool(ev)==remove and bool(out)!=remove
    assert m.decode("김민준",[(0,1),(1,2),(2,3)],["B-PERSON","I-PERSON","E-PERSON"],0)==[
        {"label":"person","start":0,"end":3}]
    print("V7_VENDOR_SELF_TEST_PASS", flush=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    args = p.parse_args()
    src = build(args.source.expanduser().resolve())
    code = compile(src, str(OUT), "exec")
    mod = types.ModuleType("v7_frozen_validation")
    exec(code, mod.__dict__)
    self_test(mod)
    if OUT.exists():
        if OUT.read_text(encoding="utf-8") != src:
            raise FileExistsError("V7_FROZEN_SOURCE_CHANGED: inspect before replacing")
        print("V7_VENDOR_ALREADY_IDENTICAL", flush=True)
    else:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUT.parent,
                                         prefix=".v7_frozen_", delete=False) as f:
            f.write(src)
            temp = Path(f.name)
        try:
            temp.replace(OUT)
        finally:
            temp.unlink(missing_ok=True)
        print("V7_VENDOR_COMPLETE", OUT.relative_to(ROOT), flush=True)

if __name__ == "__main__":
    main()
