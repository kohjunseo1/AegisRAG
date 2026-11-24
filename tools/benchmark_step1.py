"""Read-only, aggregate-only Step1 benchmark for the integration branch.

Run each backend in a fresh process, with the same CPU/runtime and Gold.
No span cache, network, DB or HMAC. Legacy is the unmodified live path.
A V7 source must be implemented and verified before selecting it.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata as md
import json
import math
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import subprocess
import sys
import time

LABELS = frozenset(("person", "address", "phone_number", "email",
                    "account_number", "card_number", "resident_registration_number"))
DATA = {
    "A187": (187, 912, "7ccf27589971ed0911756e527faf475144ab996f6d06416b4a167bda51fda822"),
    "B171": (171, 1439, "52bca604b38cf06d1445c57b9e48ad96a7654fed73dfcd0734a8eaa5df58b42f"),
    "Hard403": (403, 1958, "2497c882307e604a9359bf463c485138fe29c0ea62e9e2360338dd23c006e4ab"),
}
MODEL_HASHES = {
    "roberta_small_v1": {
        "config.json": "d8d486cc3d59fec1ff109eaf6a60e22b53dc451d4dccb14f94e4b93fcf0b07ac",
        "model.safetensors": "05dd28640e2c884007b7c7668ad1e2f770ac2cabe0d31cc9cfe6ac02d2b69b4f",
        "tokenizer.json": "200c2fd5a65daf3b67aa532740d3a26736deceaa8b28e82826038aede9377cf0",
        "tokenizer_config.json": "fd172592aef04c3c1c5be31fa2072c27ae81bdc4192b4e919ea0db6b43407913",
    },
    "koelectra_ft5": {
        "config.json": "6eb2b024f6144b5df04be1304997765aa8518420998ab1f3151a50f570ba0f91",
        "model.safetensors": "fd2a89454ef29acac263ce3ea3629ee6b5eb513216d46f599462e5b179922abc",
        "tokenizer.json": "6234fb96e9031ec5e6a83b27ba1ade973e823d5e34a62a49c8d00a20f49e09eb",
        "tokenizer_config.json": "8a4a9ae2d88ba2638f862c7df7564b38ac4b2849f24c850752a72b9a1e3908b8",
    },
}
ONEPASS_SHA = "978a1c8a7100d2c2411ce797c72cb08b235dcf150f77d3003e4f05881bd8bd1a"
# Explicit integration profile from f8d7caf .env.example, not ambient .env.
LEGACY = dict(PII_HYBRID="1", PII_HYBRID_TAU_ACCEPT="0.5",
              PII_HYBRID_TAU_REVIEW="0.5", PII_HYBRID_DROP_UNAGREED="0",
              PII_HYBRID_NUM_UNION="1", PII_HYBRID_NAME_FILTER="none",
              PII_SURNAME_TITLE_IS_PII="0", PII_ENSEMBLE="0",
              PII_BOOST_PERSON="0", PII_BOOST_ADDRESS="1",
              PII_V6="inproc", PII_V6_URL="")
COMMON = dict(PII_SPANS_CACHE="", PII_SPANS_FALLBACK="0",
              PII_SPANS_FILTER="0", PII_NUM_THREADS="2",
              HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
              TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="2",
              MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2",
              VECLIB_MAXIMUM_THREADS="2", RAYON_NUM_THREADS="2")

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def require(ok, code):
    if not ok:
        raise RuntimeError(code)

def keys(items, n):
    require(isinstance(items, list), "INVALID_SPANS")
    out = set()
    for x in items:
        l, a, b = x.get("label"), x.get("start"), x.get("end")
        require(l in LABELS and type(a) is int and type(b) is int
                and 0 <= a < b <= n, "INVALID_SPAN")
        out.add((l, a, b))
    require(len(out) == len(items), "DUPLICATE_SPANS")
    return out

def load_data(name, path):
    n, gold_n, sha = DATA[name]
    require(digest(path) == sha, "DATA_HASH_MISMATCH_" + name)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    require(len(rows) == n, "DOC_COUNT_MISMATCH")
    ids = set()
    total = 0
    for r in rows:
        text = r["text"]
        require(isinstance(text, str), "INVALID_TEXT")
        rid = str(r["id"])
        require(rid not in ids, "DUPLICATE_DOC")
        ids.add(rid)
        spans = keys(r["entities"], len(text))
        for e in r["entities"]:
            require(text[e["start"]:e["end"]] == e.get("text"), "GOLD_TEXT_MISMATCH")
        ordered = sorted((a, b) for _, a, b in spans)
        require(all(b <= c for (_, b), (c, _) in zip(ordered, ordered[1:])), "GOLD_OVERLAP")
        total += len(spans)
    require(total == gold_n, "GOLD_COUNT_MISMATCH")
    return rows

def score(c, spans, gold, length):
    p, g = keys(spans, length), keys(gold, length)
    c["tp"] += len(p & g)
    c["fp"] += len(p - g)
    c["fn"] += len(g - p)
    mask = bytearray(length)
    for _, a, b in p:
        mask[a:b] = b"\x01" * (b-a)
    for _, a, b in g:
        hit = sum(mask[a:b])
        c["gold_chars"] += b-a
        c["covered_chars"] += hit
        c["fully_uncovered"] += hit == 0
        c["partly_uncovered_only"] += 0 < hit < b-a

def metrics(c):
    tp, fp, fn = (c[k] for k in ("tp", "fp", "fn"))
    return dict(c, strict_precision=tp/(tp+fp) if tp+fp else 0.0,
                strict_recall=tp/(tp+fn) if tp+fn else 1.0,
                strict_f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.0,
                mask_recall=c["covered_chars"]/c["gold_chars"] if c["gold_chars"] else 1.0,
                leak_chars=c["gold_chars"]-c["covered_chars"])

def self_test():
    c = Counter()
    score(c, [{"label":"person","start":0,"end":2}],
          [{"label":"person","start":0,"end":3}], 4)
    m = metrics(c)
    assert (m["tp"],m["fp"],m["fn"],m["mask_recall"],m["partly_uncovered_only"]) == (0,1,1,2/3,1)
    c = Counter()
    score(c, [{"label":"address","start":0,"end":3}],
          [{"label":"person","start":0,"end":3}], 4)
    assert metrics(c)["mask_recall"] == 1 and metrics(c)["strict_f1"] == 0
    print("SCORER_SELF_TEST_PASS", flush=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=("legacy","v7_onepass"), default="legacy")
    p.add_argument("--team-model-root", type=Path)
    p.add_argument("--v7-checkpoint", type=Path)
    p.add_argument("--data", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", type=Path)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test()
        return
    require(a.data and a.out and (a.limit is None or a.limit > 0), "MISSING_ARGUMENTS")
    require(not a.out.exists(), "OUTPUT_ALREADY_EXISTS")
    datasets = {}
    for item in a.data:
        name, path = item.split("=", 1)
        require(name in DATA and name not in datasets, "UNKNOWN_OR_DUPLICATE_DATASET")
        datasets[name] = load_data(name, Path(path).expanduser())
    os.environ.update(COMMON, PII_BACKEND=a.backend)
    if a.backend == "legacy":
        require(a.team_model_root is not None, "TEAM_MODELS_REQUIRED")
        root = a.team_model_root.expanduser().resolve()
        for model, files in MODEL_HASHES.items():
            for filename, sha in files.items():
                require(digest(root/model/filename) == sha, "TEAM_MODEL_HASH_MISMATCH_" + model)
        os.environ.update(LEGACY, PII_MODEL_DIR=str(root/"roberta_small_v1"),
                          PII_HYBRID_MODEL=str(root/"koelectra_ft5"))
    else:
        require(a.v7_checkpoint is not None, "V7_CHECKPOINT_REQUIRED")
        ck = a.v7_checkpoint.expanduser().resolve()
        require(digest(ck/"model.safetensors") == ONEPASS_SHA, "V7_MODEL_HASH_MISMATCH")
        os.environ.update(PII_V7_MODEL_DIR=str(ck), PII_V6="", PII_V6_URL="",
                          PII_HYBRID="0", PII_ENSEMBLE="0")
    def deny(*args, **kwargs):
        raise RuntimeError("NETWORK_FORBIDDEN")
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    started = time.perf_counter()
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    from starter import pii_pipeline as P
    if a.backend == "legacy":
        from starter import v6_client
        require(P.HYBRID and not P.ENSEMBLE and v6_client.MODE == "inproc", "LEGACY_PROFILE_MISMATCH")
    else:
        require(getattr(P, "BACKEND", None) == "v7_onepass", "V7_ADAPTER_NOT_IMPLEMENTED")
    def detect(text):
        spans, info = P.detect_info(text)
        expected = "merge" if a.backend == "legacy" else "v7_onepass"
        require(info.get("source") == expected, "BACKEND_SOURCE_MISMATCH")
        if a.backend == "legacy":
            require(info.get("v6_status") in ("READY_FOR_PSEUDONYMIZATION","REVIEW_REQUIRED"),
                    "V6_BAD_STATUS")
            require(v6_client.STATS["v6_fail"] == 0, "V6_FALLBACK_FORBIDDEN")
        return spans
    warmup = "합성 테스트 고객 홍길동 님의 연락처는 010-1234-5678 입니다."
    detect(warmup)
    cold_ms = (time.perf_counter()-started)*1000
    for _ in range(2):
        detect(warmup)
    print("MODEL_READY", a.backend, "cold_ms=", round(cold_ms,2), flush=True)
    results = {}
    for name, rows in datasets.items():
        c, times = Counter(), []
        selected = rows[:a.limit] if a.limit else rows
        for i, row in enumerate(selected,1):
            t = time.perf_counter()
            spans = detect(row["text"])
            times.append((time.perf_counter()-t)*1000)
            score(c, spans, row["entities"], len(row["text"]))
            if i % 25 == 0 or i == len(selected):
                print("PROGRESS", a.backend, name, f"{i}/{len(selected)}", flush=True)
        results[name] = dict(metrics(c), docs=len(selected), dataset_sha256=DATA[name][2],
                             mean_ms=statistics.mean(times),
                             p95_ms=sorted(times)[math.ceil(.95*len(times))-1])
        print("RESULT", name, json.dumps(results[name], sort_keys=True), flush=True)
    versions = {}
    for pkg in ("torch","transformers","tokenizers","numpy","onnxruntime","pii-detector"):
        try:
            versions[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            versions[pkg] = None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = dict(backend=a.backend, smoke=a.limit is not None,
                  model_count=4 if a.backend=="legacy" else 1,
                  git_head=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
                  python=sys.version.split()[0], platform=platform.platform(),
                  versions=versions, cold_start_ms=cold_ms,
                  peak_rss_mb=rss/(1024**2 if sys.platform=="darwin" else 1024),
                  cpu_threads=2, datasets=results, profile=LEGACY if a.backend=="legacy" else {},
                  mask_definition="coverage of final typed spans returned by common entrypoint",
                  gate_e2e_validated=False, repeat=1)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("x", encoding="utf-8") as f:
        json.dump(report,f,indent=2,sort_keys=True)
        f.write("\n")
    print("BENCHMARK_COMPLETE", a.backend, flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Do not leak arbitrary library exceptions containing source text.
        if type(e) is RuntimeError and str(e).isupper():
            print("BENCHMARK_FAILED", str(e), flush=True)
        else:
            print("BENCHMARK_FAILED", type(e).__name__, flush=True)
        raise SystemExit(1) from None
