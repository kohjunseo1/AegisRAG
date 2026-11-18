#!/usr/bin/env python3
"""Read-only, Gold-based OFFLINE DIAGNOSTIC for HCX confusion sensitivity.

Conditions for the *same* real retrieved Top-5, same masked question and HCX
baseline configuration:
  gold_only         : only Gold documents that really appeared in Top-5
  gold_confusable   : Gold + Gold-labeled confusable documents in Top-5
  actual_top5       : actual unchanged Top-5, including neutral/other documents

Gold and confusable labels control diagnostic candidate subsets ONLY. Never
put Gold categories/reasons into an HCX prompt or production retrieval policy.

Default is PLAN-ONLY: no HCX calls. --live needs explicit external transfer
approval and writes owner-only private JSONL, never prints answers/PII.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / "step2_privacy_rag" / "local_eval").resolve()
sys.path.insert(0, str(ROOT / "scripts"))

ID = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
DEFAULT_IDS = ("Q052", "Q075", "Q027", "Q048", "Q062")
CONDITIONS = ("gold_only", "gold_confusable", "actual_top5")


def private_input(path: Path) -> Path:
    """Input lives in a private local_eval directory (possibly sibling worktree)."""
    p = path.expanduser().resolve()
    if "local_eval" not in p.parts or p.suffix != ".jsonl" or not p.is_file():
        raise ValueError("CONF_PRIVATE_INPUT_REQUIRED")
    return p


def read_gold(path: Path) -> dict[str, dict]:
    rows = {}
    for line in private_input(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        qid = row.get("qid")
        rel, conf = row.get("relevant_doc_ids"), row.get("confusable_doc_ids", [])
        if (not isinstance(qid, str) or not ID.fullmatch(qid) or qid in rows
                or not isinstance(row.get("query"), str)
                or not isinstance(rel, list) or not isinstance(conf, list)
                or any(not isinstance(i, str) or not ID.fullmatch(i) for i in rel + conf)
                or set(rel) & set(conf)):
            raise ValueError("CONF_INVALID_GOLD")
        rows[qid] = row
    if not rows:
        raise ValueError("CONF_EMPTY_GOLD")
    return rows


def choose_cases(cases: list[dict], gold: dict[str, dict], qids: list[str]) -> list[dict]:
    lookup = {c["case_id"]: c for c in cases}
    if not qids or len(qids) != len(set(qids)):
        raise ValueError("CONF_QIDS_INVALID")
    selected = []
    for qid in qids:
        if qid not in lookup or qid not in gold:
            raise ValueError("CONF_QID_NOT_FOUND")
        case, label = lookup[qid], gold[qid]
        # Detect accidental mixing of different Holdout versions.
        if case["question"] != label["query"]:
            raise ValueError("CONF_QUESTION_GOLD_MISMATCH")
        if (case.get("relevant_doc_ids") is not None
                and set(case["relevant_doc_ids"]) != set(label["relevant_doc_ids"])):
            raise ValueError("CONF_RETRIEVAL_GOLD_MISMATCH")
        selected.append(case)
    return selected


def build_conditions(top5: list[dict], gold_ids: set[str],
                     confusable_ids: set[str]) -> list[dict]:
    """Pure, deterministic design; no Gold used by runtime outside diagnosis."""
    if len(top5) > 5 or len({d["doc_id"] for d in top5}) != len(top5):
        raise ValueError("CONF_INVALID_TOP5")
    if gold_ids & confusable_ids:
        raise ValueError("CONF_OVERLAPPING_GOLD")
    relevant = [d for d in top5 if d["doc_id"] in gold_ids]
    confusable = [d for d in top5 if d["doc_id"] in confusable_ids]
    # Only if a confusable actually entered Top-5 can injection be tested.
    gold_plus = [d for d in top5
                 if d["doc_id"] in gold_ids or d["doc_id"] in confusable_ids]
    result = [
        {"condition":"gold_only", "docs":relevant,
         "skip_reason":None if relevant else "NO_GOLD_IN_ACTUAL_TOP5"},
        {"condition":"gold_confusable", "docs":gold_plus,
         "skip_reason":None if relevant and confusable else
                        ("NO_GOLD_IN_ACTUAL_TOP5" if not relevant else
                         "NO_CONFUSABLE_IN_ACTUAL_TOP5")},
        {"condition":"actual_top5", "docs":list(top5),
         "skip_reason":None if top5 else "EMPTY_ACTUAL_TOP5"},
    ]
    seen = {}
    for c in result:
        if c["skip_reason"] is not None:
            c["duplicate_of"] = None
            continue
        ids = tuple(d["doc_id"] for d in c["docs"])
        c["duplicate_of"] = seen.get(ids)
        if ids not in seen:
            seen[ids] = c["condition"]
    return result


def safe_row(case: dict, cond: dict, relevant: set[str],
             confusable: set[str], trial: int) -> dict:
    ids = [d["doc_id"] for d in cond["docs"]]
    return {
        "case_id":case["case_id"],"category":case.get("category"),
        "trial":trial, "condition":cond["condition"],
        "document_ids":ids, "document_count":len(ids),
        "gold_included":sum(i in relevant for i in ids),
        "confusable_included":sum(i in confusable for i in ids),
        "skipped":cond["skip_reason"],"duplicate_of":cond["duplicate_of"],
    }


def run(args: argparse.Namespace) -> None:
    from eval_chat_mvp import load_cases
    all_cases = load_cases(private_input(args.cases))
    labels = read_gold(args.gold)
    selected = choose_cases(all_cases, labels, args.qids)

    if not 1 <= args.trials <= 3:
        raise ValueError("CONF_TRIALS_MUST_BE_1_TO_3")
    if os.environ.get("AEGISRAG_HCX_EVIDENCE_MODE", "baseline") != "baseline":
        raise ValueError("CONF_REQUIRES_BASELINE_MODE")
    if args.live:
        if os.environ.get("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED") != "YES":
            raise ValueError("CONF_EXTERNAL_TRANSFER_NOT_APPROVED")
        if args.out is None:
            raise ValueError("CONF_OUTPUT_REQUIRED")
        dest = args.out.expanduser().resolve()
        if (not dest.is_relative_to(PRIVATE) or dest.suffix != ".jsonl"
                or dest.exists() or dest in (args.cases.resolve(),args.gold.resolve())):
            raise ValueError("CONF_OUTPUT_MUST_BE_NEW_PRIVATE_JSONL")
    else:
        dest = None

    sys.path.insert(0, str(ROOT / "step2_privacy_rag" / "app"))
    from core import LivePipeline

    pipeline = LivePipeline(ROOT,device=args.device,reuse_step1=True)
    try:
        status = pipeline.index_status()
        if status["status"] != "READY":
            raise ValueError("CONF_INDEX_NOT_READY")
        t0 = time.perf_counter()
        pipeline.preload_models()
        print("CONF_MODEL_PRELOAD_MS",round((time.perf_counter()-t0)*1000,2),flush=True)

        # Preflight the entire read-only selection before any external call.
        prepared_work = []
        count = 0
        for case in selected:
            label = labels[case["case_id"]]
            gate, prepared, top5 = pipeline.ask_local(case["question"])
            if gate != "READY" or prepared is None:
                prepared_work.append((case,label,None,gate,[]))
                print("CONF_GATE",json.dumps({"case_id":case["case_id"],"state":gate},
                                              sort_keys=True),flush=True)
                continue
            gold = set(label["relevant_doc_ids"])
            confusable = set(label.get("confusable_doc_ids",[]))
            conditions = build_conditions(top5,gold,confusable)
            prepared_work.append((case,label,prepared,gate,conditions))
            count += sum(c["skip_reason"] is None and c["duplicate_of"] is None
                         for c in conditions) * args.trials
            print("CONF_PLAN",json.dumps({
                "case_id":case["case_id"], "gold_in_top5":sum(d["doc_id"] in gold for d in top5),
                "confusable_in_top5":sum(d["doc_id"] in confusable for d in top5),
                "conditions":[{
                    "name":c["condition"],
                    "ids":[d["doc_id"] for d in c["docs"]],
                    "skip":c["skip_reason"],"duplicate_of":c["duplicate_of"]
                } for c in conditions]
            },sort_keys=True),flush=True)
        print("CONF_PREFLIGHT",json.dumps({
            "selected_cases":len(selected),"planned_hcx_calls":count,
            "trials":args.trials,"live":args.live,
            "gold_only_from_actual_top5":True,
            "gold_sent_to_hcx_as_label":False,
            "db_write":False
        },sort_keys=True),flush=True)
        if not args.live:
            return

        # Create an isolated owner-only private report directory if absent.
        # dest has already been resolved and checked against PRIVATE.
        dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # No output content or raw question is ever written to stdout.
        from hcx import OutboundError, call_hcx, redact_residual_pii
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(dest, flags, 0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as output:
            for case,label,prepared,gate,conditions in prepared_work:
                if gate != "READY":
                    output.write(json.dumps({"case_id":case["case_id"],"gate_state":gate,
                        "trial":None,"condition":None,"skipped":"GATE_NOT_READY"},
                        sort_keys=True)+"\n")
                    output.flush()
                    continue
                relevant = set(label["relevant_doc_ids"])
                confusable = set(label.get("confusable_doc_ids",[]))
                for trial in range(1,args.trials+1):
                    done = {}
                    for cond in conditions:
                        row = safe_row(case,cond,relevant,confusable,trial)
                        row["gate_state"] = gate
                        if cond["skip_reason"]:
                            row["decision"] = None
                            row["answer"] = None
                        elif cond["duplicate_of"]:
                            previous = done[cond["duplicate_of"]]
                            row.update({k:previous.get(k) for k in (
                                "decision","answer","citations","gold_citations",
                                "confusable_citations","other_citations",
                                "input_tokens","output_tokens","hcx_ms","error")})
                            row["reused"] = True
                        else:
                            t = time.perf_counter()
                            row["reused"] = False
                            try:
                                result,usage,_ = call_hcx(ROOT,prepared.text,cond["docs"],
                                                          bool(prepared.regions))
                                text,_ = redact_residual_pii(result["answer"])
                                ids = result["citations"]
                                row.update({
                                    "decision":result["decision"],
                                    "answer":text,
                                    "citations":ids,
                                    "gold_citations":[i for i in ids if i in relevant],
                                    "confusable_citations":[i for i in ids if i in confusable],
                                    "other_citations":[i for i in ids if
                                                       i not in relevant and i not in confusable],
                                    "input_tokens":usage.get("prompt_tokens"),
                                    "output_tokens":usage.get("completion_tokens"),
                                })
                            except OutboundError as exc:
                                code = str(exc)
                                row["error"] = (code if re.fullmatch(r"[A-Z0-9_]{1,80}",code)
                                                else "HCX_OUTBOUND_ERROR")
                                if row["error"] == "HCX_HTTP_429":
                                    row["rate_limit_diagnostics"] = exc.diagnostics
                                row["decision"] = None
                                row["answer"] = None
                            row["hcx_ms"] = round((time.perf_counter()-t)*1000,2)
                        done[cond["condition"]] = row
                        output.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")
                        output.flush()
                        print("CONF_RESULT",json.dumps({
                            "case_id":row["case_id"],"condition":row["condition"],
                            "trial":trial,"decision":row.get("decision"),
                            "gold_citations":row.get("gold_citations",[]),
                            "confusable_citations":row.get("confusable_citations",[]),
                            "other_citations":row.get("other_citations",[]),
                            "error":row.get("error"),"reused":row.get("reused",False),
                            "skipped":row.get("skipped"),
                            "rate_limit_diagnostics":row.get("rate_limit_diagnostics")
                        },sort_keys=True),flush=True)
                        if row.get("error") == "HCX_HTTP_429":
                            print("CONF_RATE_LIMIT_STOP",json.dumps({
                                "case_id":row["case_id"],
                                "condition":row["condition"],
                                "trial":trial,
                                "report":str(dest),
                                "rate_limit_diagnostics":row.get("rate_limit_diagnostics",{})
                            },sort_keys=True),flush=True)
                            return
        print("CONF_DONE",json.dumps({"report":str(dest),
            "note":"Citation overlap is not factual correctness. Compare private answers with source evidence.",
            "no_db_writes":True},sort_keys=True),flush=True)
    finally:
        pipeline.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases",type=Path,required=True,
                    help="Existing private e2e_pilot_17.jsonl")
    ap.add_argument("--gold",type=Path,required=True,
                    help="Private ORIGINAL Holdout Gold JSONL with confusable_doc_ids")
    ap.add_argument("--qids",nargs="+",default=list(DEFAULT_IDS))
    ap.add_argument("--device",choices=("cpu","mps"),default="cpu")
    ap.add_argument("--trials",type=int,default=1)
    ap.add_argument("--live",action="store_true",
                    help="Actually call HCX; must explicitly approve external transfer")
    ap.add_argument("--out",type=Path,help="New, owner-only report in local_eval")
    args=ap.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Never log message strings of arbitrary exceptions; may contain private data.
        code = str(exc) if isinstance(exc,ValueError) and re.fullmatch(
            r"CONF_[A-Z0-9_]{1,80}",str(exc)) else type(exc).__name__
        print("CONF_FAILED",code,file=sys.stderr,flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
