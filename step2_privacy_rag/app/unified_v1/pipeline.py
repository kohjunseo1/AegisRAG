"""Local preprocessing orchestration; caller supplies runtimes and key in memory."""
from dataclasses import dataclass
from .contracts import PreparationResult, ProtectionPlan, DetectionEvidence
from .protection import V6GateAdapter, prepare
from .replay import SnapshotGateAdapter
from .identity import IdentityBundle, extract_owned_fields
from .retrieval import QuerySpec

@dataclass(frozen=True, repr=False)
class PreparedInput:
    preparation: PreparationResult
    plan: ProtectionPlan
    identity: IdentityBundle | None
    evidence: DetectionEvidence | None = None

class LocalPipeline:
    def __init__(self,engine,baseline_detector=None, *, hmac_key,key_tag,reuse_raw=True,fusion_policy='evidence_v2'):
        self.engine=engine
        self.gate=SnapshotGateAdapter(policy=fusion_policy) if reuse_raw else V6GateAdapter(baseline_detector)
        self.key,self.key_tag=hmac_key,key_tag

    def prepare(self,text, *, input_kind,doc_id):
        evidence=self.engine.detect(text,input_kind=input_kind)
        plan=self.gate.plan(text,evidence)
        prepared=prepare(text,plan,input_kind=input_kind,hmac_key=self.key,key_tag=self.key_tag)
        identity=None
        if prepared.state=='LOCAL_PREPARED':
            identity=extract_owned_fields(text,plan,doc_id=doc_id,hmac_key=self.key,key_tag=self.key_tag)
        return PreparedInput(prepared,plan,identity,evidence)

def query_from_mentions(preparation,approval,mentions, *, regions=()):
    """Lookup-only/UNRESOLVED mentions cannot silently become confirmed identity."""
    mentions=tuple(mentions)
    unknown=[m for m in mentions if m.link_status!='LINKED' or m.entity_id is None]
    lookup_only=bool(unknown) and all(m.name_lookup_key and not m.attribute_key_ids for m in unknown)
    unresolved=bool(unknown) and not lookup_only
    ids=tuple(sorted({m.entity_id for m in mentions if m.link_status=='LINKED' and m.entity_id}))
    return QuerySpec(preparation,approval,regions,ids,unresolved,lookup_only,
                     tuple(sorted({m.name_lookup_key for m in mentions if m.name_lookup_key})))
