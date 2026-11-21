"""A.X V7 data/model preflight and continued 37-label fine-tuning.

Designed for explicit synthetic/authorized training data only. Do NOT pass
old Official Test, Locked, Holdout, public hard_eval or synth_fresh as training
or model-selection data. No model is ever pushed while training.

Data JSONL: {"id":"...", "text":"...", "entities":[{"label":"person",
"start":0,"end":3}], "annotation_scope":"seven_only"|"full_37"}.
seven_only means all non-annotated tokens are UNKNOWN (loss label -100).
full_37 means all PII classes in the 37-tag vocabulary were annotated;
only use on truly fully labeled source documents.

Run from repository root. See reports/unified_v1/V7_SINGLE_AX_RUNBOOK.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

SOURCE_REPO = "PII-detect/pii-ax-mixed-v1"
SOURCE_REVISION = "c0f99b1dc113befefeff1a3091087d50bc3ea635"
DEST_REPO = "PII-detect/pii-ax-single-v7"
LABELS7 = (
    "person", "phone_number", "email", "address",
    "resident_registration_number", "account_number", "card_number",
)
ALIASES = {
    "person": ("PERSON", "NAME", "FULL_NAME", "KOREAN_NAME", "PERSON_NAME", "PRIVATE_PERSON"),
    "phone_number": ("PHONE", "PHONE_NUMBER", "PHONE_NUM", "MOBILE", "MOBILE_PHONE", "TELEPHONE", "PRIVATE_PHONE"),
    "email": ("EMAIL", "E_MAIL", "EMAIL_ADDRESS", "PRIVATE_EMAIL"),
    "address": ("ADDRESS", "STREET_ADDRESS", "HOME_ADDRESS", "POSTAL_ADDRESS", "PRIVATE_ADDRESS"),
    "resident_registration_number": ("RRN", "RESIDENT_REGISTRATION_NUMBER", "RESIDENT_NUMBER", "RESIDENT_ID", "KOREAN_RRN", "JUMIN"),
    "account_number": ("ACCOUNT", "ACCOUNT_NUMBER", "ACCOUNT_NUM", "BANK_ACCOUNT", "BANK_ACCOUNT_NUMBER"),
    "card_number": ("CARD", "CARD_NUMBER", "CARD_NUM", "CREDIT_CARD", "CREDIT_CARD_NUMBER", "DEBIT_CARD", "PAYMENT_CARD"),
}
UNSAFE_SPLIT = re.compile(r"(^|[/_\-.])(test|holdout|locked|blind|hard_eval|synth_fresh)([/_\-.]|$)", re.I)


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_fast_tokenizer(model_dir):
    """Match frozen Step1 backend compatibility for older A.X tokenizer_config."""
    import shutil
    import tempfile
    from transformers import AutoTokenizer, PreTrainedTokenizerFast
    try:
        tok = AutoTokenizer.from_pretrained(
            str(model_dir), use_fast=True, local_files_only=True)
    except ValueError as ex:
        if "TokenizersBackend" not in str(ex):
            raise
        try:
            tok = PreTrainedTokenizerFast.from_pretrained(
                str(model_dir), local_files_only=True)
        except Exception:
            with tempfile.TemporaryDirectory(prefix="ax-v7-tokenizer-") as tmp:
                tmp_dir=Path(tmp)
                for file in Path(model_dir).iterdir():
                    if file.is_file() and file.suffix not in (".safetensors",".bin"):
                        shutil.copy2(file,tmp_dir/file.name)
                config=tmp_dir/"tokenizer_config.json"
                if not config.is_file():
                    raise ValueError("TOKENIZER_COMPAT_CONFIG_MISSING")
                meta=json.loads(config.read_text(encoding="utf-8"))
                meta["tokenizer_class"]="PreTrainedTokenizerFast"
                config.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
                tok=AutoTokenizer.from_pretrained(str(tmp_dir),use_fast=True,local_files_only=True)
    if not tok.is_fast:
        raise ValueError("FAST_TOKENIZER_REQUIRED_FOR_UNICODE_OFFSETS")
    return tok


def load_rows(path, *, role):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix != ".jsonl":
        raise ValueError(f"{role}: expected an existing .jsonl file")
    if role in ("train", "replay", "dev") and UNSAFE_SPLIT.search(path.name):
        raise ValueError(f"{role}: withheld/test-named data cannot be used to train or select checkpoints")
    if role == "dev" and re.search(r"(generated[_-]?val|group[_-]?val|official[_-]?dev)", path.name, re.I):
        raise ValueError("DEV_LEAKAGE_RISK: previous epoch-selection or training dev cannot select V7")
    rows, seen = [], set()
    for line in path.open(encoding="utf-8-sig"):
        if not line.strip():
            continue
        x = json.loads(line)
        if not isinstance(x.get("id"), (str, int)) or not isinstance(x.get("text"), str):
            raise ValueError(f"{role}: id/text schema invalid")
        if x["id"] in seen:
            raise ValueError(f"{role}: duplicate id")
        seen.add(x["id"])
        if x.get("annotation_scope") not in ("seven_only", "full_37"):
            raise ValueError(f"{role}: annotation_scope must be seven_only or full_37")
        ents = x.get("entities")
        if not isinstance(ents, list):
            raise ValueError(f"{role}: entities must be an array")
        o_regions = x.get("verified_o_spans", [])
        if not isinstance(o_regions, list):
            raise ValueError(f"{role}: verified_o_spans must be an array")
        intervals = []
        for ent in ents:
            label, a, b = ent.get("label"), ent.get("start"), ent.get("end")
            if not isinstance(label, str) or type(a) is not int or type(b) is not int:
                raise ValueError(f"{role}: entity label/offset invalid")
            if not 0 <= a < b <= len(x["text"]):
                raise ValueError(f"{role}: span out of range")
            if "text" in ent and ent["text"] != x["text"][a:b]:
                raise ValueError(f"{role}: gold substring mismatch")
            if x["annotation_scope"] == "seven_only" and label not in LABELS7:
                raise ValueError(f"{role}: seven_only contains non-seven class")
            intervals.append((a, b))
        intervals.sort()
        if any(intervals[i][0] < intervals[i-1][1] for i in range(1, len(intervals))):
            raise ValueError(f"{role}: overlapping Gold spans not supported")
        checked_o = []
        for region in o_regions:
            if not isinstance(region, dict):
                raise ValueError(f"{role}: verified O interval must be a mapping")
            a, b = region.get("start"), region.get("end")
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(x["text"]):
                raise ValueError(f"{role}: verified O interval invalid")
            if region.get("text") != x["text"][a:b]:
                raise ValueError(f"{role}: verified O substring mismatch")
            if any(a < e and s < b for s,e in intervals):
                raise ValueError(f"{role}: verified O overlaps positive Gold")
            checked_o.append((a,b))
        checked_o.sort()
        if any(checked_o[i][0] < checked_o[i-1][1] for i in range(1,len(checked_o))):
            raise ValueError(f"{role}: verified O spans overlap")
        if len(x["text"]) < 2:
            raise ValueError(f"{role}: empty or invalid text")
        rows.append(x)
    if not rows:
        raise ValueError(f"{role}: dataset empty")
    return path, rows


def check_splits(sets):
    signatures = {}
    for label, (_, rows) in sets.items():
        hashes = {hashlib.sha256(re.sub(r"\s+", "", x["text"]).encode()).hexdigest() for x in rows}
        for earlier, other in signatures.items():
            if hashes & other:
                raise ValueError(f"DATA_LEAK: duplicate normalized texts across {label} and {earlier}")
        signatures[label] = hashes


def find_model_dir(model_dir):
    if model_dir:
        root = Path(model_dir).expanduser().resolve()
    else:
        from huggingface_hub import snapshot_download
        root = Path(snapshot_download(
            repo_id=SOURCE_REPO, revision=SOURCE_REVISION,
            allow_patterns=["best_model/*"], repo_type="model"))
    local = root / "best_model" if (root / "best_model" / "config.json").is_file() else root
    if not (local / "config.json").is_file():
        raise ValueError("A.X 37-label config not found; provide --model-dir root or best_model")
    return local


def read_labels(model_dir):
    conf = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    idx = {int(k): v for k, v in conf["id2label"].items()}
    if len(idx) != 37 or sorted(idx) != list(range(37)) or len(set(idx.values())) != 37:
        raise ValueError("AX_BASE_LABELS_NOT_37_OR_NOT_UNIQUE")
    def norm(label):
        return re.sub(r"[\s_-]+", "_", label.strip().upper())
    labels = {norm(name): i for i, name in idx.items()}
    names = {}
    for typ, options in ALIASES.items():
        matches = [(a, labels.get("B_" + a), labels.get("I_" + a))
                   for a in options if "B_" + a in labels and "I_" + a in labels]
        if len(matches) != 1:
            raise ValueError(f"AX_BASE_LABEL_MAPPING_AMBIGUOUS_OR_MISSING:{typ}:{matches}")
        names[typ] = (matches[0][1], matches[0][2])
    if "O" not in labels:
        raise ValueError("AX_BASE_MISSING_O")
    return idx, labels, names, labels["O"]


def create_alignment(rows, tokenizer, idx, labels, names, o_id, max_length):
    # A fully-labeled row may use original 37-class entity type names.
    # On seven-only data, -100 on all unlabeled tokens avoids false negatives.
    dataset = []
    for row in rows:
        tokens = tokenizer(row["text"], return_offsets_mapping=True,
                           truncation=False, add_special_tokens=True)
        if len(tokens["input_ids"]) > max_length:
            raise ValueError("TOKENIZED_ROW_TOO_LONG: split sentence/doc into short, offset-verified training examples")
        offsets = tokens.pop("offset_mapping")
        # Some V7 tokenizer snapshots emit BERT-style segment IDs. ModernBERT
        # token classification does not accept token_type_ids in forward().
        # These IDs are not needed for single-document sequence labeling.
        tokens.pop("token_type_ids", None)
        targets = [o_id if row["annotation_scope"] == "full_37" else -100] * len(offsets)
        for n, (a, b) in enumerate(offsets):
            if a == b:
                targets[n] = -100
        for region in row.get("verified_o_spans", []):
            start, end = region["start"], region["end"]
            for i, (a,b) in enumerate(offsets):
                # Only tokens *fully* inside an audited non-PII region get O.
                if a < b and start <= a and b <= end:
                    targets[i] = o_id
        for ent in row["entities"]:
            entity_label = ent["label"]
            if entity_label in names:
                b_id, i_id = names[entity_label]
            else:
                if row["annotation_scope"] != "full_37":
                    raise ValueError("UNKNOWN_ENTITY_LABEL_ON_PARTIAL_ROW")
                tag = re.sub(r"[\s_-]+", "_", entity_label.strip().upper())
                if "B_" + tag not in labels or "I_" + tag not in labels:
                    raise ValueError("ENTITY_LABEL_NOT_IN_BASE_37")
                b_id, i_id = labels["B_" + tag], labels["I_" + tag]
            touched = [i for i, (a, b) in enumerate(offsets)
                       if a < b and a < ent["end"] and ent["start"] < b]
            if not touched:
                raise ValueError("ENTITY_WITH_NO_ALIGNED_TOKEN")
            first_a = offsets[touched[0]][0]
            last_b = offsets[touched[-1]][1]
            if first_a != ent["start"] or last_b != ent["end"]:
                raise ValueError(
                    "ENTITY_TOKEN_BOUNDARY_MISMATCH: cannot safely supervise "
                    "a token that crosses the annotated PERSON/ADDRESS edge")
            for j, i in enumerate(touched):
                if targets[i] not in (-100, o_id):
                    raise ValueError("TOKEN_OVERLAPS_MULTIPLE_GOLD_SPANS")
                targets[i] = b_id if j == 0 else i_id
        tokens["labels"] = targets
        dataset.append(tokens)
    return dataset


def report_set(name, path, rows):
    c = Counter(e["label"] for row in rows for e in row["entities"])
    counts = Counter(x["annotation_scope"] for x in rows)
    print("DATASET_OK", name, json.dumps({
        "sha256": checksum(path), "documents": len(rows),
        "annotations": dict(sorted(c.items())),
        "annotation_scopes": dict(counts),
        "verified_o_spans": sum(len(row.get("verified_o_spans", [])) for row in rows),
    }, ensure_ascii=False), flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("preflight", "train", "upload"))
    cli.add_argument("--model-dir", type=Path, help="existing pinned HF snapshot or best_model directory")
    cli.add_argument("--train", type=Path, help="new seven_only/full_37 labeled synthetic data")
    cli.add_argument("--replay", type=Path, help="old ORIGINAL fully annotated 37-class training documents")
    cli.add_argument("--dev", type=Path, help="fresh, independently created dev data; not old official test")
    cli.add_argument("--out", type=Path, default=Path("step2_privacy_rag/local_eval/ax_v7"))
    cli.add_argument("--max-length", type=int, default=384)
    cli.add_argument("--epochs", type=float, default=2.0)
    cli.add_argument("--lr", type=float, default=1.0e-5)
    cli.add_argument("--batch-size", type=int, default=2)
    cli.add_argument("--grad-accum", type=int, default=8)
    cli.add_argument("--replay-fraction", type=float, default=0.35)
    cli.add_argument("--distill-weight", type=float, default=0.5)
    cli.add_argument("--seed", type=int, default=42)
    cli.add_argument("--hf-read-check", action="store_true")
    cli.add_argument("--confirm-upload-to", default=None,
                     help="Exact new PII-detect/ destination repo; never overwrite existing")
    args = cli.parse_args()
    if args.max_length < 64 or args.max_length > 512:
        cli.error("--max-length must be 64..512 for safe pilot")
    if not 0 < args.replay_fraction <= 1 or not 0 <= args.distill_weight <= 1:
        cli.error("invalid replay fraction / distillation weight")
    if args.action == "upload":
        return upload(args)

    if not all((args.train, args.replay, args.dev)):
        cli.error("preflight/train requires --train, --replay, and --dev")
    sets = {}
    for name in ("train", "replay", "dev"):
        sets[name] = load_rows(getattr(args, name), role=name)
    partial_replay = any(x["annotation_scope"] == "seven_only" for x in sets["replay"][1])
    if partial_replay and args.distill_weight < 0.5:
        raise ValueError("SEVEN_ONLY_REPLAY_REQUIRES_DISTILL_WEIGHT_GE_0_5: unannotated 37-class tokens are UNKNOWN, not O")
    if partial_replay:
        print("REPLAY_WARNING partial seven-label annotations; full 37-class replay unavailable. "
              "Teacher KL preserves old outputs but does not guarantee no forgetting.", flush=True)
    check_splits(sets)
    model_dir = find_model_dir(args.model_dir)
    idx, labels, names, o_id = read_labels(model_dir)
    tokenizer = load_fast_tokenizer(model_dir)
    for name, (path, rows) in sets.items():
        report_set(name, path, rows)
        create_alignment(rows, tokenizer, idx, labels, names, o_id, args.max_length)
        for row in rows:
            for e in row["entities"]:
                if e["label"] not in names and (
                    row["annotation_scope"] != "full_37" or
                    "B_" + re.sub(r"[\s_-]+", "_", e["label"].upper()) not in labels
                ):
                    raise ValueError(f"{name}: annotation label does not match the model's 37-label head")
    if args.hf_read_check:
        from huggingface_hub import HfApi, get_token
        if not get_token():
            raise ValueError("HF_LOGIN_REQUIRED: use hf auth login or HF_TOKEN in your local shell")
        api = HfApi()
        user = api.whoami()
        model = api.model_info(SOURCE_REPO, revision=SOURCE_REVISION)
        print("HF_READ_OK", {"authenticated": bool(user), "source_revision_matches": model.sha == SOURCE_REVISION})
        # Read checks CANNOT guarantee write permissions. Never test via unapproved remote write.
    print("AX_V7_37_LABEL_PREFLIGHT_PASS", json.dumps({
        "model_source": SOURCE_REPO, "revision": SOURCE_REVISION,
        "head_labels": len(idx), "mapped_step1_types": len(names),
        "source_dir": str(model_dir),
    }), flush=True)
    if args.action == "preflight":
        return 0
    return train(args, sets, model_dir, idx, labels, names, o_id)


def train(args, sets, model_dir, idx, labels, names, o_id):
    import random
    import torch
    from torch.utils.data import Dataset
    from torch.nn import functional as F
    from transformers import (AutoModelForTokenClassification, AutoTokenizer,
                              DataCollatorForTokenClassification, Trainer, TrainingArguments)

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    tok = load_fast_tokenizer(model_dir)
    training_rows = list(sets["train"][1])
    old_rows = list(sets["replay"][1])
    replay_n = min(len(old_rows), max(1, int(len(training_rows)*args.replay_fraction)))
    random.Random(args.seed).shuffle(old_rows)
    training_rows.extend(old_rows[:replay_n])
    random.Random(args.seed).shuffle(training_rows)
    train_data = create_alignment(training_rows, tok, idx, labels, names, o_id, args.max_length)
    dev_data = create_alignment(sets["dev"][1], tok, idx, labels, names, o_id, args.max_length)
    def positive_tokens(data):
        return sum(any(int(i) != -100 and int(i) != o_id for i in x["labels"]) for x in data)
    if not positive_tokens(train_data) or not positive_tokens(dev_data):
        raise ValueError("TRAIN_AND_DEV_REQUIRE_POSITIVE_LABELED_TOKENS")
    print("TRAIN_ALIGNMENT_OK", json.dumps({
        "train_examples":len(train_data),"dev_examples":len(dev_data),
        "replay_examples":replay_n,"train_positive_docs":positive_tokens(train_data)
    }), flush=True)

    class Rows(Dataset):
        def __init__(self, samples): self.samples=samples
        def __len__(self): return len(self.samples)
        def __getitem__(self, i): return self.samples[i]

    model = AutoModelForTokenClassification.from_pretrained(
        str(model_dir), local_files_only=True)
    teacher = AutoModelForTokenClassification.from_pretrained(
        str(model_dir), local_files_only=True)
    if model.config.num_labels != 37 or teacher.config.id2label != model.config.id2label:
        raise ValueError("ORIGINAL_37_LABEL_WEIGHTS_CHANGED")
    teacher.eval()
    for p in teacher.parameters(): p.requires_grad_(False)

    class RetainTrainer(Trainer):
        def __init__(self, *a, reference_model, retain_weight, **kw):
            super().__init__(*a, **kw)
            self.reference_model = reference_model
            self.retain_weight = retain_weight

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels = inputs["labels"]
            features = {k:v for k,v in inputs.items() if k != "labels"}
            outputs = model(**features)
            logits = outputs.logits
            has_supervision = (labels != -100).any()
            ce = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]),
                                 labels.reshape(-1), ignore_index=-100) if has_supervision else logits.sum()*0
            if self.retain_weight:
                self.reference_model.to(logits.device)
                with torch.no_grad():
                    reference = self.reference_model(**features).logits
                temp = 2.0
                kl = F.kl_div(
                    F.log_softmax(logits.float()/temp,dim=-1),
                    F.softmax(reference.float()/temp,dim=-1),
                    reduction="none").sum(-1) * temp * temp
                mask = features["attention_mask"].float()
                retain = (kl * mask).sum() / mask.sum().clamp_min(1)
                loss = ce + self.retain_weight * retain
            else:
                loss = ce
            return (loss, outputs) if return_outputs else loss

    out = args.out.expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("OUTPUT_NOT_EMPTY: protect previous runs; supply a fresh --out directory")
    out.mkdir(parents=True, exist_ok=True)
    args_hf = TrainingArguments(
        output_dir=str(out/"checkpoints"),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        save_total_limit=2,
        report_to="none",
        logging_strategy="epoch",
        seed=args.seed,
        data_seed=args.seed,
        fp16=False,
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        remove_unused_columns=False,
        push_to_hub=False,
    )
    trainer = RetainTrainer(
        model=model,args=args_hf,processing_class=tok,
        train_dataset=Rows(train_data),eval_dataset=Rows(dev_data),
        data_collator=DataCollatorForTokenClassification(
            tok,label_pad_token_id=-100,padding=True),
        reference_model=teacher,retain_weight=args.distill_weight)
    trainer.train()
    metrics = trainer.evaluate()
    best = out/"best_model"
    trainer.save_model(str(best))
    tok.save_pretrained(str(best))
    if read_labels(best)[0] != idx:
        raise ValueError("OUTPUT_37_LABEL_PARITY_FAILED")
    report = {
        "source":SOURCE_REPO,"source_revision":SOURCE_REVISION,
        "target_repo":DEST_REPO,"head_labels":37,
        "data_sha256":{k:checksum(v[0]) for k,v in sets.items()},
        "dev_metrics":{k:float(v) for k,v in metrics.items() if isinstance(v,(int,float))},
        "config":{"epochs":args.epochs,"lr":args.lr,"seed":args.seed,
                  "replay_fraction":args.replay_fraction,"distill_weight":args.distill_weight,
                  "max_length":args.max_length},
        "status":"LOCAL_CANDIDATE_UNVALIDATED_NOT_FOR_DEPLOYMENT",
    }
    (out/"training_manifest.json").write_text(
        json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("AX_V7_TRAINED_LOCALLY_ONLY", json.dumps({
        "checkpoint":str(best),"head_labels":37,
        "dev_loss":report["dev_metrics"].get("eval_loss"),
        "requires_separate_strict_and_gate_evaluation":True,
        "pushed_to_hf":False
    }),flush=True)
    return 0


def upload(args):
    if args.confirm_upload_to != DEST_REPO:
        raise ValueError("UPLOAD_REQUIRES_EXACT_CONFIRMATION: --confirm-upload-to " + DEST_REPO)
    out = args.out.expanduser().resolve()
    best, manifest = out/"best_model",out/"training_manifest.json"
    if not (best/"config.json").is_file() or not manifest.is_file():
        raise ValueError("LOCAL_CHECKPOINT_AND_MANIFEST_REQUIRED")
    read_labels(best)
    report = json.loads(manifest.read_text(encoding="utf-8"))
    if report.get("status") != "LOCAL_CANDIDATE_UNVALIDATED_NOT_FOR_DEPLOYMENT":
        raise ValueError("UNEXPECTED_MANIFEST_STATUS")
    from huggingface_hub import HfApi, get_token
    if not get_token():
        raise ValueError("HF_WRITE_TOKEN_REQUIRED: authenticate locally, never paste token in chat")
    api = HfApi()
    api.whoami()
    try:
        api.model_info(DEST_REPO)
    except Exception as ex:
        from huggingface_hub.utils import RepositoryNotFoundError
        if not isinstance(ex, RepositoryNotFoundError):
            raise
    else:
        raise ValueError("TARGET_REPO_ALREADY_EXISTS: never overwrite a prior candidate")
    api.create_repo(DEST_REPO, private=True, repo_type="model", exist_ok=False)
    api.upload_folder(
        folder_path=str(best),repo_id=DEST_REPO,
        repo_type="model",path_in_repo="best_model",
        commit_message="Add AX V7 37-label candidate; not production approved")
    api.upload_file(
        path_or_fileobj=str(manifest),path_in_repo="training_manifest.json",
        repo_id=DEST_REPO,repo_type="model",
        commit_message="Add training provenance and provisional status")
    print("AX_V7_PRIVATE_UPLOAD_COMPLETE", DEST_REPO,flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
