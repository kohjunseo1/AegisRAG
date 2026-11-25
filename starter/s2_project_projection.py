"""Opt-in LOCAL search representation. Never an egress/declassification permit.
Apply project scope only AFTER strict preparation or validated local recovery.
Keep strict output separately. Gold, QIDs, and dataset words are not inputs.
"""
from dataclasses import dataclass
import re
from .s2_pseudonymizer import READY,pseudonymize_local
from .s2_query_controller import region_paths,_key
from .v7_context_validated import ROLE
from . import v7_frozen as F

PROJECT_SCOPE=frozenset({"event_status_nominal","bereavement_kinship_role",
    "candidate_bereavement_body","candidate_bereavement_header",
    "standalone_surname_title","candidate_surname_title","explicit_internal_extension"})

@dataclass(frozen=True,repr=False)
class LocalProjection:
    strict:object
    search:object
    released:tuple
    region_keys:tuple
    external_transfer_approved:bool=False

def overlaps(a,b,e):
    return a<e["end"] and e["start"]<b

def intervals(bits):
    out=[];i=0
    while i<len(bits):
        if not bits[i]:i+=1;continue
        a=i
        while i<len(bits) and bits[i]:i+=1
        out.append(dict(start=a,end=i))
    return out

# Distinguish a grammatical address-field reference from a road-name suffix.
# Keep actual numeric/road/dong tails protected; ambiguous forms remain strict.
_FIELD_REFERENCE=re.compile(r"^\s+(?:주소|거주지|소재지)(?:로|으로)\s+(?:된|등록된|기재된|입력된|표기된)(?=\s|$|[,.;?!])")
_DETAIL_TAIL=re.compile(r"\s*(?:[0-9]|[가-힣0-9]+(?:로|길|번지|동|리|읍|면|호)(?=\s|[0-9]|$|[,.)]))")

def has_address_detail(tail):
    return bool(_DETAIL_TAIL.match(tail)) and not bool(_FIELD_REFERENCE.match(tail))

def _tail_self_test():
    for t in (" 주소로 된 고객", " 소재지로 등록된 사업장", " 거주지로 기재된 고객"):
        assert not has_address_detail(t)
    for t in (" 가상로 25", " 주소로 25", " 가상길", " 101동", " 25", " 가상동"):
        assert has_address_detail(t)

def project(text,strict,meta,*,key,query=False,region_lookup=lambda _:False):
    """Requires a verified strict output; never operate on an unresolved doc.
    Regional recognition is a heuristic plus observed scoped document key,
    not proof of public/non-identifying information. Only local projection.
    """
    typed=[{k:e[k] for k in ("start","end","label")} for e in strict.safe_token_manifest]
    jobs=strict.replacement_intervals
    bits=bytearray(len(text))
    for j in jobs:bits[j["start"]:j["end"]]=b"\x01"*(j["end"]-j["start"])
    pending=meta.get("review_events",[])
    audit=meta.get("audit_events",[])
    paths=region_paths(text) if query else []
    region=None
    if len(paths)==1:
        a,b,p=paths[0]
        k=_key(key,p)
        # Do not treat a leading region of an apparent detailed address as
        # standalone. Unknown formats stay strict; downstream intent still required.
        tail=text[b:]
        detail=has_address_detail(tail)
        crossing=any(overlaps(a,b,e) and not (a<=e["start"]<e["end"]<=b and e.get("label")=="address") for e in typed+pending)
        if not detail and not crossing and region_lookup(k):region=(a,b,k)
    removed=set();released=[]
    for j in jobs:
        a,b=j["start"],j["end"];kind=j["kind"];reason=None
        ev=[e for e in audit if type(e.get("start")) is int and type(e.get("end")) is int and overlaps(a,b,e)]
        pe=[e for e in pending if type(e.get("start")) is int and type(e.get("end")) is int and overlaps(a,b,e)]
        if kind=="residual" and not pe and ev and all(e.get("source") in PROJECT_SCOPE for e in ev):
            covered={i for e in ev for i in range(max(a,e["start"]),min(b,e["end"]))}
            if len(covered)==b-a and not any(overlaps(a,b,e) for e in typed):
                reason="approved_project_residual"
        if region and region[0]<=a<b<=region[1]:
            if kind=="hmac" and j.get("label")=="address" and not pe:
                reason="regional_typed_candidate"
            elif kind=="residual" and pe and all(e.get("label")=="address" and region[0]<=e["start"]<e["end"]<=region[1] for e in pe) and not any(overlaps(a,b,e) for e in typed):
                covered={i for e in pe for i in range(max(a,e["start"]),min(b,e["end"]))}
                if len(covered)==b-a and not ev:reason="regional_review_candidate"
        if query and kind=="hmac" and j.get("label")=="person" and b-a==1 and not pe and not ev:
            for m in ROLE.finditer(text):
                if m.span("s")== (a,b) and m.group("s") in F.SURNAMES and not any(overlaps(*m.span(),e) and (e["start"],e["end"])!=(a,b) for e in typed):
                    if not F.PERSON_FIELD.search(text[max(0,a-75):a]):
                        reason="explicit_surname_title";break
        if reason:
            bits[a:b]=b"\0"*(b-a);removed.add((a,b))
            released.append(dict(start=a,end=b,reason=reason))
    revised=[e for e in typed if (e["start"],e["end"]) not in removed]
    result=strict
    if released:
        result=pseudonymize_local(text,dict(decision=READY,entities=revised,
            entities_for_pseudonymization=revised,mask_intervals=intervals(bits),
            redaction_intervals=[]),hmac_key=key,include_vault_records=True)
    return LocalProjection(strict,result,tuple(released),(region[2],) if region else ())


if __name__=="__main__":
    _tail_self_test()
    print("ADDRESS_TAIL_SELF_TEST_PASS",flush=True)
