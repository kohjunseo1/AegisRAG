"""V7 final-onepass candidate: one encoder, frozen inference, audited Core policy.

Evaluation-only until Step2 consumes typed entities and protection separately.
No fallback, no remote downloads, no Gold in inference.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

_LOCK = RLock()
_detector = None
POLICY = "core_postal_join_guard_v1_audited"

def apply_policy(text, raw):
    from . import v7_frozen as F
    raw = F.validate_spans(text, raw)
    bounded, boundary_events = F.validate_boundaries(text, raw)
    core, exclusions = F.refine_spans(text, bounded)
    joined, join_events = F.postal_merge(text, F.validate_spans(text, core))
    typed, guard_events = F.apply_guard(text, joined)
    typed = F.validate_spans(text, typed)
    # Never silently unmask candidates suppressed for strict scoring.
    # Actual release decision is deferred to the Step2 privacy gate.
    mask = F.mask_union(raw, typed, text)
    review = []
    for e in boundary_events + join_events:
        if e.get("status","").startswith("review"):
            review.append({"source": e.get("rule","boundary") + ":" + e["status"],
                           "label": "person" if e.get("rule")=="name_suffix" else "address",
                           "start": e.get("start"), "end": e.get("end")})
    for e in exclusions:
        review.append({"source": e["rule"], "label": e["label"], "start": e["start"], "end": e["end"]})
    for e in guard_events:
        review.append({"source": e["reason"], "label": e["label"], "start": e["start"], "end": e["end"]})
    for i, a in enumerate(typed):
        for b in typed[i+1:]:
            if b["start"] >= a["end"]:
                break
            if F.overlaps(a,b):
                review.append({"source":"typed_overlap","label":b["label"],
                               "start":b["start"],"end":b["end"]})
    return typed, {"source":"v7_onepass", "policy":POLICY, "raw_spans":raw,
                   "mask_intervals":mask, "review":review,
                   "boundary_events":boundary_events, "structural_events":exclusions,
                   "join_events":join_events, "guard_events":guard_events,
                   "status":"REVIEW_REQUIRED" if review else "READY_FOR_PSEUDONYMIZATION",
                   "deployment_approved":False}

class Onepass:
    def __init__(self, path):
        from . import v7_frozen as F
        import torch
        from transformers import AutoModelForTokenClassification
        path = Path(path).expanduser().resolve()
        if not (path/"config.json").is_file() or not (path/"model.safetensors").is_file():
            raise FileNotFoundError("V7_CHECKPOINT_MISSING")
        self.torch, self.F = torch, F
        print("V7_TOKENIZER_LOADING", flush=True)
        self.tokenizer = F.load_fast_tokenizer(path)
        self.special_pair = F.tokenizer_special_pair(self.tokenizer)
        print("V7_MODEL_LOADING", flush=True)
        self.model = AutoModelForTokenClassification.from_pretrained(
            str(path), local_files_only=True).to("cpu").eval()
        self.model.config.torchscript = False
        self.model.config.return_dict = True
        if self.model.config.model_type != "modernbert" or self.model.config.num_labels != 37:
            raise ValueError("V7_MODEL_CONFIG_MISMATCH")
        print("V7_MODEL_LOADED", flush=True)

    def detect(self, text):
        if not isinstance(text,str):
            raise TypeError("V7_TEXT_REQUIRED")
        with self.torch.inference_mode():
            raw, calls = self.F.token_window_predict(
                text,self.tokenizer,self.model,self.torch,self.F.decode,
                "cpu",510,128,special_pair=self.special_pair)
        spans, info = apply_policy(text,raw)
        info["forward_calls"] = calls
        return spans, info

def detect_info(text):
    global _detector
    with _LOCK:
        if _detector is None:
            path = os.environ.get("PII_V7_MODEL_DIR")
            if not path:
                raise RuntimeError("V7_CHECKPOINT_REQUIRED")
            _detector = Onepass(path)
        return _detector.detect(text)
