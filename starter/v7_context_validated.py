"""Step1 review resolution candidate: shared context policy, no new model.

Keep typed entities and EVERY previous protection offset unchanged.
Moving an event to audit never authorizes unmasking or external release.
No Gold, document IDs, dataset-specific words or confidence retuning.
"""
from __future__ import annotations
import re
from . import v7_frozen as F
from .v7_validated import ValidatedOnepass, cover

POLICY="core_postal_join_guard_seed25_adjacent10_context_v2"
SOURCE="o_pii_seed25_adjacent10"
# Reuse the existing approved title and surname vocabulary. Recognize
# ordinary grammatical continuations, not an arbitrary substring of a name.
ROLE=re.compile(
    r"(?<![가-힣A-Za-z0-9])(?P<s>[가-힣])[ \t]+"+F.TITLE+
    r"(?:님)?(?:께서|에게|한테|께|입니다|이라고|이라며|은|는|이|가|을|를|과|와|도|의)?"
    r"(?=$|[^가-힣A-Za-z0-9])")
HANGUL=re.compile(r"[가-힣]+")
BODY_ROLE=re.compile(F.JOB_TITLE_BEFORE.pattern.removesuffix(r"\s*$")+r"[ \t]*의[ \t]*$")
DIALOGUE=re.compile(r"(?m)^[ \t]*(?:직원|상담원|상담사|참여자|화자)[ \t]+(?P<letter>[A-Z])[ \t]*[:：]")
HOST=re.compile(r"(?i)(?<![\w@])(?:https?://|www\.)(?P<host>(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}|[a-z0-9-]+\.[a-z]{2,63})(?=$|[/:?#\s)\]>,.;!])")
SERVICE_NUMBER=re.compile(r"(?<![0-9A-Za-z+@-])(?:15|16|18)[0-9]{2}-[0-9]{4}(?![0-9A-Za-z-])")
SERVICE_CONTEXT=re.compile(r"고객\s*센터|콜\s*센터|대표\s*(?:번호|전화)|문의|상담|담당|연락처|전화번호")
AMOUNT=re.compile(r"(?<![\w*])\*{2,}[ \t]*원(?=$|[^0-9A-Za-z])")

def contained(e,a,b):
    return a<=e["start"]<e["end"]<=b

def touches(a,b,spans):
    return any(a<x["end"] and x["start"]<b for x in spans)

def person_reason(text,e,typed):
    a,b=e["start"],e["end"]
    for m in ROLE.finditer(text):
        if m.group("s") not in F.SURNAMES or not contained(e,*m.span()):
            continue
        # Existing policy applies only to surname/title, not a larger name.
        if touches(*m.span(),typed):
            continue
        return "candidate_surname_title"
    # Expand only for semantic classification; never expand/drop protection.
    first=text.split("\n",1)[0]
    if F.MOURNING_PREFIX.match(first):
        for m in HANGUL.finditer(text):
            s,t=m.span()
            if not contained(e,s,t) or touches(s,t,typed):
                continue
            if F.classify_nonperson(text,s,t)=="bereavement_kinship_role":
                return "candidate_bereavement_header"
            # Body: a role's kinship noun, followed by a deceased-person cue.
            # Require corresponding kinship in the obituary header and a
            # separate typed person immediately after the deceased cue.
            word=m.group()
            if not F.KINSHIP.fullmatch(word+"상") or word+"상" not in first:
                continue
            if not BODY_ROLE.search(text[max(0,s-75):s]):
                continue
            cue=re.match(r"[ \t]+故[ \t]+",text[t:])
            if cue and any(x["label"]=="person" and x["start"]==t+cue.end() for x in typed):
                return "candidate_bereavement_body"
    for m in DIALOGUE.finditer(text):
        if contained(e,*m.span("letter")) and not touches(*m.span("letter"),typed):
            return "candidate_dialogue_label"
    for m in AMOUNT.finditer(text):
        if contained(e,*m.span()) and set(text[a:b])=={"*"} and not touches(*m.span(),typed):
            return "candidate_amount_placeholder"
    return None

def reason(text,e,typed):
    if e.get("source")!=SOURCE:
        return None
    if e["label"]=="person":
        return person_reason(text,e,typed)
    if e["label"]=="email":
        for m in HOST.finditer(text):
            # Host only; no userinfo, email, port or path/query candidate.
            a,b=m.span("host")
            tail=text[b:b+1]
            if tail=="@" or not contained(e,a,b) or touches(a,b,typed):
                continue
            # Reject ambiguous URL userinfo even when it starts like a host.
            rest=re.match(r"[^\s)\]>,]*",text[b:])
            if rest and "@" in rest.group().split("/",1)[0]:
                continue
            return "candidate_url_host"
    if e["label"]=="phone_number":
        for m in SERVICE_NUMBER.finditer(text):
            if not contained(e,*m.span()) or touches(*m.span(),typed):
                continue
            line_start=text.rfind("\n",0,m.start())+1
            before=text[max(0,m.start()-120):m.start()]
            if SERVICE_CONTEXT.search(before) and SERVICE_CONTEXT.search(text[max(line_start,m.start()-65):m.start()]):
                return "candidate_service_contact"
    return None

def resolve(text,typed,meta):
    audit=list(meta["audit_events"]);pending=[]
    old_review=list(meta["review_events"])
    for e in old_review:
        why=reason(text,e,typed)
        if why:
            audit.append({**e,"source":why,"previous_source":e["source"],
                          "action":"review_resolved_protection_retained","label_confirmed":False})
        else:
            pending.append(dict(e))
    # Preserve exact protection, even for auto-resolved non-target candidates.
    # The Step2 gate must honor these intervals or block release.
    return typed,{**meta,"source":"v7_context_validated","policy":POLICY,
                  "review_before_context":old_review,
                  "audit_events":audit,"review_events":pending,"review":pending,
                  "status":"REVIEW_REQUIRED" if pending else "READY_FOR_LOCAL_PSEUDONYMIZATION",
                  "deployment_approved":False}

class ContextValidatedOnepass(ValidatedOnepass):
    def detect(self,text):
        typed,meta=super().detect(text)
        return resolve(text,typed,meta)
