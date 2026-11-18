"""Local preprocessing orchestration; caller supplies runtimes and key in memory."""
from dataclasses import dataclass
from .contracts import PreparationResult, ProtectionPlan
from .protection import V6GateAdapter, prepare
from .identity import IdentityBundle, extract_owned_fields
from .retrieval import QuerySpec

@dataclass(frozen=True, repr=False)
class PreparedInput:
    preparation: PreparationResult
    plan: ProtectionPlan
    identity: IdentityBundle | None

class LocalPipeline:
    def __init__(self,engine,baseline_detector, *, hmac_key,key_tag):
        self.engine,self.gate=engine,V6GateAdapter(baseline_detector)
        self.key,self.key_tag=hmac_key,key_tag

    def prepare(self,text, *, input_kind,doc_id):
        evidence=self.engine.detect(text,input_kind=input_kind)
        plan=self.gate.plan(text,evidence)
        prepared=prepare(text,plan,input_kind=input_kind,hmac_key=self.key,key_tag=self.key_tag)
        identity=None
        if prepared.state=='LOCAL_PREPARED':
            identity=extract_owned_fields(text,plan,doc_id=doc_id,hmac_key=self.key,key_tag=self.key_tag)
        return PreparedInput(prepared,plan,identity)

def query_from_mentions(preparation,approval,mentions, *, regions=()):
    """Lookup-only/UNRESOLVED mentions cannot silently become confirmed identity."""
    unresolved=any(m.link_status!='LINKED' or m.entity_id is None for m in mentions)
    ids=tuple(sorted({m.entity_id for m in mentions if m.link_status=='LINKED' and m.entity_id}))
    return QuerySpec(preparation,approval,regions,ids,unresolved)
