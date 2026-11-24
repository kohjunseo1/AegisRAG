"""Pinned private Hub distribution for the frozen V7 checkpoint.

Network is permitted ONLY during explicit publish/provision. Inference is local.
Fallback is the exact same model assets, never base ModernBERT or a legacy model.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import socket

REPO="PII-detect/pii-v7-onepass"
WEIGHT_SHA="978a1c8a7100d2c2411ce797c72cb08b235dcf150f77d3003e4f05881bd8bd1a"
REQUIRED={"config.json","model.safetensors","tokenizer.json","tokenizer_config.json"}
OPTIONAL={"special_tokens_map.json","added_tokens.json","vocab.txt","vocab.json","merges.txt",
          "tokenizer.model","spiece.model","sentencepiece.bpe.model"}
ASSETS=REQUIRED|OPTIONAL
DEFAULT_LOCAL=Path.home()/"AegisRAG-unified-v1/step2_privacy_rag/local_eval/models/ax_v7_final_onepass/best_model"

def require(ok,code):
    if not ok: raise RuntimeError(code)

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(4*1024**2),b""):
            h.update(chunk)
    return h.hexdigest()

def hashes(path):
    path=Path(path).expanduser().resolve()
    require(path.is_dir(),"MODEL_DIR_MISSING")
    files={n:digest(path/n) for n in sorted(ASSETS) if (path/n).is_file()}
    require(REQUIRED<=files.keys(),"MODEL_ASSETS_MISSING")
    require(files["model.safetensors"]==WEIGHT_SHA,"MODEL_WEIGHT_HASH_MISMATCH")
    cfg=json.loads((path/"config.json").read_text(encoding="utf-8"))
    require(cfg.get("model_type")=="modernbert" and len(cfg.get("id2label",{}))==37,"MODEL_CONFIG_MISMATCH")
    require(not cfg.get("auto_map"),"CUSTOM_MODEL_CODE_NOT_ALLOWED")
    return files

def validate_manifest(m):
    require(m.get("repo_id")==REPO,"REPO_MISMATCH")
    require(isinstance(m.get("revision"),str) and re.fullmatch(r"[0-9a-f]{40}",m["revision"]),"PINNED_REVISION_REQUIRED")
    h=m.get("sha256",{})
    require(isinstance(h,dict) and REQUIRED<=h.keys()<=ASSETS,"MANIFEST_ASSETS_INVALID")
    require(all(isinstance(v,str) and re.fullmatch(r"[0-9a-f]{64}",v) for v in h.values()),"MANIFEST_HASH_INVALID")
    require(h["model.safetensors"]==WEIGHT_SHA,"MANIFEST_WEIGHT_MISMATCH")

def verify(path,m):
    validate_manifest(m)
    p=Path(path).expanduser().resolve()
    require(hashes(p)==m["sha256"],"MODEL_ASSET_HASH_MISMATCH")
    return p

def read_manifest(path):
    m=json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    validate_manifest(m)
    return m

def download(m):
    from huggingface_hub import snapshot_download
    return snapshot_download(repo_id=m["repo_id"],revision=m["revision"],
                             allow_patterns=sorted(m["sha256"]),token=True)

def provision(m,fallback=None,*,fetch=None):
    validate_manifest(m)
    if fetch is None:fetch=download
    try:
        path=fetch(m)
    except Exception as exc:
        # Only exceptions from acquisition, never from integrity verification
        # or model execution, can activate the explicit same-model fallback.
        print("HUB_ACQUISITION_FAILED",type(exc).__name__,flush=True)
        if fallback is None:raise
        return verify(fallback,m),"verified_local_fallback"
    return verify(path,m),"verified_hub_snapshot"

def publish(path,out):
    from huggingface_hub import HfApi
    path=Path(path).expanduser().resolve()
    out=Path(out).expanduser().resolve()
    require(not out.exists(),"MANIFEST_OUTPUT_EXISTS")
    local=hashes(path)
    api=HfApi(token=True)
    api.create_repo(REPO,repo_type="model",private=True,exist_ok=True)
    info=api.model_info(REPO)
    require(info.private is True,"PRIVATE_REPO_REQUIRED")
    existing={x.rfilename for x in (info.siblings or [])}
    if "model.safetensors" in existing:
        # Safe rerun: never overwrite an existing weights revision.
        revision=info.sha
        print("EXISTING_REPO_VERIFY_ONLY",flush=True)
    else:
        result=api.upload_folder(repo_id=REPO,repo_type="model",folder_path=str(path),
                                 allow_patterns=sorted(local),commit_message="Publish frozen V7 onepass checkpoint")
        revision=result.oid
    m=dict(repo_id=REPO,revision=revision,sha256=local)
    validate_manifest(m)
    remote=verify(download(m),m)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("x",encoding="utf-8") as f:json.dump(m,f,indent=2,sort_keys=True)
    print("HUB_PUBLISH_VERIFIED",REPO,revision,flush=True)
    print("MANIFEST",out,flush=True)
    print("VERIFIED_SNAPSHOT",remote,flush=True)
    return m

def load_local(manifest_path,model_dir):
    """Future Step2 entrypoint. Never performs a download or silently swaps models."""
    from .v7_context_validated import ContextValidatedOnepass
    return ContextValidatedOnepass(verify(model_dir,read_manifest(manifest_path)))

def smoke(m,fallback):
    primary,source=provision(m) # no fallback: prove actual pinned Hub acquisition
    print("PRIMARY",source,flush=True)
    def unavailable(_):
        raise OSError("SIMULATED_HUB_UNAVAILABLE")
    backup,bsource=provision(m,fallback,fetch=unavailable)
    require(bsource=="verified_local_fallback","FALLBACK_NOT_TESTED")
    print("FALLBACK",bsource,flush=True)
    # An integrity failure after acquisition must NEVER select the fallback.
    try:
        provision(m,fallback,fetch=lambda _:Path("/nonexistent-v7-model"))
    except RuntimeError as e:
        require(str(e)=="MODEL_DIR_MISSING","INTEGRITY_NEGATIVE_CONTROL")
    else:
        raise RuntimeError("UNSAFE_INTEGRITY_FALLBACK")
    from tools.benchmark_step1 import COMMON,load_data,require as check
    from tools.benchmark_v7_context_validated import self_test
    os.environ.update(COMMON)
    def deny(*args,**kwargs):raise RuntimeError("NETWORK_FORBIDDEN_DURING_INFERENCE")
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import torch
    from .v7_context_validated import ContextValidatedOnepass
    torch.set_num_threads(2);torch.set_num_interop_threads(2)
    self_test()
    home=Path.home()
    a=load_data("A187",home/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl")
    b=load_data("B171",home/"Downloads/blind_eval.jsonl")
    cases=[("A187",1,a[0]["text"],False),("A187",57,a[56]["text"],True),
           ("B171",1,b[0]["text"],False),("B171",34,b[33]["text"],True)]
    def evaluate(p):
        det=ContextValidatedOnepass(p)
        outputs=[]
        for name,idx,text,review in cases:
            typed,meta=det.detect(text)
            check(bool(meta["review_events"])==review,"SMOKE_REVIEW_MISMATCH")
            check(meta["deployment_approved"] is False,"UNAPPROVED_RELEASE")
            outputs.append((typed,{k:meta[k] for k in ("raw_spans","mask_intervals","review_events","audit_events","status","forward_calls")}))
        del det;gc.collect()
        return outputs
    expected=evaluate(backup)
    actual=evaluate(primary)
    require(actual==expected,"HUB_LOCAL_INFERENCE_PARITY_MISMATCH")
    print("HUB_LOCAL_SMOKE_PASS",len(cases),"documents; same raw/typed/mask/review/audit/status",flush=True)
    print("FALLBACK_SMOKE_PASS",flush=True)
    print("MODEL_PROVISION_READY",primary,flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("publish","provision","smoke"))
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--local",type=Path,default=None)
    a=p.parse_args()
    os.umask(0o077)
    if a.action=="publish":publish(a.local or DEFAULT_LOCAL,a.manifest)
    elif a.action=="smoke":smoke(read_manifest(a.manifest),a.local or DEFAULT_LOCAL)
    else:
        path,source=provision(read_manifest(a.manifest),a.local)
        print("MODEL_SOURCE",source,flush=True)
        print("PII_V7_MODEL_DIR",path,flush=True)

if __name__=="__main__":main()
