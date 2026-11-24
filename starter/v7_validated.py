"""Frozen Step1 validated candidate. No DB/UI/egress integration.
Single encoder, unchanged typed entities, conservative review protection.
Only offset metadata; deployment remains explicitly unapproved.
"""
from __future__ import annotations
import math
from . import v7_frozen as F
from .v7_onepass import Onepass, apply_policy

POLICY = "core_postal_join_guard_seed25_adjacent10_v1"
SUPPRESSION = {"standalone_surname_title","explicit_internal_extension",
               "event_status_nominal","bereavement_kinship_role"}

def require(ok,code):
    if not ok: raise RuntimeError(code)

def key(e):
    return e["label"],e["start"],e["end"]

def cover(spans,n):
    m=bytearray(n)
    for e in spans:
        a,b=e["start"],e["end"]
        require(type(a) is int and type(b) is int and 0<=a<b<=n,"INVALID_PROTECTION_OFFSET")
        m[a:b]=b"\x01"*(b-a)
    return m

class CaptureTokenizer:
    def __init__(self,tok):
        self.tok=tok
        self.enc=None
    def __call__(self,*args,**kwargs):
        self.enc=self.tok(*args,**kwargs)
        return self.enc

class CaptureModel:
    def __init__(self,model,torch):
        self.model=model
        self.config=model.config
        self.torch=torch
        self.rows=[]
        self.tags=[self.config.id2label[i] for i in range(self.config.num_labels)]
        self.types=[F.model_label(t) for t in self.tags]
        oi=[i for i,t in enumerate(self.tags) if str(t).upper()=="O"]
        require(len(oi)==1,"O_LABEL_NOT_UNIQUE")
        self.oi=oi[0]
        self.groups={lab:[i for i,v in enumerate(self.types) if v==lab] for lab in F.LABELS}
        require(all(self.groups.values()),"MISSING_PII_LABEL_GROUP")
        self.group_names=list(self.groups)
    def __call__(self,**kwargs):
        result=self.model(**kwargs)
        logits=result.logits[0,1:-1]
        require(bool(self.torch.isfinite(logits).all().item()),"NONFINITE_LOGITS")
        prob=logits.float().softmax(dim=-1)
        grouped=self.torch.stack([prob[:,ids].sum(dim=-1) for ids in self.groups.values()],dim=-1)
        best_p,best_group=grouped.max(dim=-1)
        self.rows.append(list(zip(logits.argmax(dim=-1).tolist(),prob[:,self.oi].tolist(),
                                  best_p.tolist(),best_group.tolist(),grouped.sum(dim=-1).tolist())))
        return result

def capture(det,text):
    tok=CaptureTokenizer(det.tokenizer)
    model=CaptureModel(det.model,det.torch)
    with det.torch.inference_mode():
        raw,calls=F.token_window_predict(text,tok,model,det.torch,F.decode,"cpu",510,128,
                                         special_pair=det.special_pair)
    enc=tok.enc
    offsets=enc["offset_mapping"] if enc is not None else []
    require(len(model.rows)==calls,"CAPTURE_CALL_COUNT_MISMATCH")
    windows=F.window_bounds(len(offsets),510,128)
    owners=F.winning_ranges(windows,len(offsets))
    return raw,calls,offsets,windows,owners,model

def owner_rows(offsets,windows,owners,model):
    selected=[None]*len(offsets)
    for (a,b),(ka,kb),rows in zip(windows,owners,model.rows):
        require(len(rows)==b-a,"BAD_CAPTURE_LENGTH")
        for i in range(ka,kb):
            require(selected[i] is None,"DUP_OWNER")
            selected[i]=rows[i-a]
    require(all(r is not None for r in selected),"MISSING_OWNER")
    return selected

def expand(text,tokens,protected,allow_space=False):
    """25% seeds with adjacent 10% same-best-type support. No arbitrary word list."""
    groups=[]; current=[]
    def flush():
        if current and any(t["p_pii"]>=.25 for t in current):
            groups.extend(dict(label=t["best_type"],start=t["start"],end=t["end"])
                          for t in current)
        current.clear()
    for t in tokens:
        a,b=t["start"],t["end"]
        require(0<=a<=b<=len(text),"INVALID_TOKEN_OFFSET")
        if (a==b or not text[a:b].strip() or str(t["winner"]).upper()!="O"
                or t["p_pii"]<.10 or all(protected[a:b])):
            flush()
            continue
        if current:
            prev=current[-1]
            gap=text[prev["end"]:a] if a>=prev["end"] else ""
            connected=(a<=prev["end"] or (allow_space and gap in (" ","\t")))
            if not connected or prev["best_type"]!=t["best_type"]:
                flush()
        current.append(t)
    flush()
    return sorted({key(e):e for e in groups}.values(),key=key)

def postal_resolved(event,typed,info):
    """Only clear previously reported overlap if final join actually covers it.
    Never use Gold to decide a review signal.
    """
    if event["source"]!="postal:review_overlap":
        return False
    a,b=event["start"],event["end"]
    if not isinstance(a,int) or not isinstance(b,int) or a>=b:
        return False
    joined=[j for j in info["join_events"] if j.get("status")=="merged"
            and j.get("start",a+1)<=a and j.get("end",b-1)>=b]
    if not joined:
        return False
    hits=[e for e in typed if e["start"]<b and a<e["end"]]
    return len(hits)==1 and hits[0]["label"]=="address" and hits[0]["start"]<=a and hits[0]["end"]>=b


def split_core_events(typed,info):
    audit=[]; review=[]
    for e in info["review"]:
        if e["source"] in SUPPRESSION:
            audit.append({**e,"action":"strict_excluded_protection_retained"})
        elif postal_resolved(e,typed,info):
            audit.append({**e,"action":"resolved_by_postal_join"})
        else:
            review.append(dict(e))
    for e in info["boundary_events"]+info["join_events"]:
        if not e.get("status","").startswith("review"):
            audit.append({**e,"source":e.get("rule","boundary"),"action":e["status"]})
    return audit,review

def finalize(text,typed,info,tokens):
    audit,review=split_core_events(typed,info)
    # Exactly the selected adjacent-only policy. Candidates are NOT typed PII.
    candidates=expand(text,tokens,cover(info["mask_intervals"],len(text)))
    # Group touching same-type candidates for review UI without widening coverage.
    merged=[]
    for e in sorted(candidates,key=lambda e:(e["label"],e["start"],e["end"])):
        if merged and merged[-1]["label"]==e["label"] and e["start"]<=merged[-1]["end"]:
            merged[-1]["end"]=max(merged[-1]["end"],e["end"])
        else:
            merged.append(dict(e))
    for e in merged:
        review.append({**e,"source":"o_pii_seed25_adjacent10",
                       "action":"unresolved_candidate","label_confirmed":False})
    # mask_union accepts typed spans; original raw and typed reproduce original union.
    protection=F.mask_union(info["raw_spans"],[*typed,*candidates],text)
    require(all(not old or new for old,new in zip(cover(info["mask_intervals"],len(text)),
            cover(protection,len(text)))),"PROTECTION_REGRESSION")
    return typed,{**info,"source":"v7_validated","policy":POLICY,
                  "entities":typed,"core_mask_intervals":info["mask_intervals"],
                  "mask_intervals":protection,"uncertainty_candidates":candidates,
                  "audit_events":audit,"review_events":review,"review":review,
                  "status":"REVIEW_REQUIRED" if review else "READY_FOR_LOCAL_PSEUDONYMIZATION",
                  "deployment_approved":False}

class ValidatedOnepass(Onepass):
    def detect(self,text):
        if not isinstance(text,str):
            raise TypeError("V7_TEXT_REQUIRED")
        raw,calls,offsets,windows,owners,model=capture(self,text)
        typed,info=apply_policy(text,raw)
        tokens=[]
        for (a,b),r in zip(offsets,owner_rows(offsets,windows,owners,model)):
            winner,po,pg,gi,pa=r
            tokens.append({"start":int(a),"end":int(b),"winner":str(model.tags[winner]),
                           "p_pii":pa,"best_type":model.group_names[gi]})
        result,meta=finalize(text,typed,info,tokens)
        meta["forward_calls"]=calls
        meta["unmapped_non_o_uncovered_tokens"]=sum(
            1 for t,r in zip(tokens,owner_rows(offsets,windows,owners,model))
            if t["start"]<t["end"] and t["winner"]!="O" and model.types[r[0]] is None
            and text[t["start"]:t["end"]].strip()
            and not any(x["start"]<=t["start"] and x["end"]>=t["end"] for x in meta["core_mask_intervals"]))
        return result,meta
