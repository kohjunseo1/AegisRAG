"""HCX-007 schema adapter. Autonomous path permits mock transport only."""
from __future__ import annotations
from dataclasses import dataclass, field
import copy
import json
import re
from hcx import SCHEMA, SYSTEM, redact_residual_pii, validate_outbound, validate_response
from .contracts import ContractError, DetectionEvidence, RetrievedEvidence, fingerprint
from .protection import StructuralSentinel, ContextualBoost
from .retrieval import TOKEN, DOCID, validate_receipt

class PrivacyRecheck:
    """Independent raw detector plus residual patterns. Required model failure blocks.

    Valid 96-bit tokens are blanked preserving codepoints before independent NER;
    arbitrary/malformed tokens are rejected, not silently redacted or trusted.
    """
    def __init__(self, independent_engine):
        self.engine=independent_engine

    def check(self,text, *, allowed_tokens=None):
        if not isinstance(text,str) or len(text)>20000:
            raise ContractError('RECHECK_INVALID_TEXT')
        tokens=set(TOKEN.findall(text))
        if allowed_tokens is not None and not tokens<=set(allowed_tokens):
            raise ContractError('UNAUTHORIZED_OUTPUT_TOKEN')
        blank=TOKEN.sub(lambda m:' '*len(m[0]),text)
        # Prevent short/unknown-label tokens from escaping independent checks.
        if re.search(r'<[A-Za-z_]+_',blank):
            raise ContractError('MALFORMED_OR_UNKNOWN_TOKEN')
        _,counts=redact_residual_pii(blank)
        if any(counts.values()) or StructuralSentinel().candidates(blank) or ContextualBoost().candidates(blank):
            raise ContractError('RESIDUAL_PII_DETECTED')
        if self.engine is None:
            raise ContractError('INDEPENDENT_RECHECK_UNAVAILABLE')
        try:
            evidence=self.engine.detect(blank,input_kind='query')
            if (not isinstance(evidence,DetectionEvidence) or not evidence.ready
                    or evidence.original_input_fingerprint!=fingerprint(blank)):
                raise ValueError
            for source,rows in evidence.raw_candidates_by_source.items():
                for row in rows:
                    row.validate(blank)
                    if row.source_id!=source:raise ValueError
            if any(evidence.raw_candidates_by_source.values()) or evidence.structural_candidates:
                raise ContractError('RESIDUAL_PII_DETECTED')
        except ContractError:
            raise
        except Exception:
            raise ContractError('INDEPENDENT_RECHECK_FAILED') from None
        return True

@dataclass(frozen=True, repr=False)
class Context:
    question: str
    documents: tuple[dict,...]
    allowed_tokens: frozenset[str]
    index_version: str
    has_region: bool

class ContextBuilder:
    def __init__(self,guard, *, key_tag,policy_version,index_version,corpus_id):
        self.guard=guard
        self.key_tag,self.policy_version,self.index_version,self.corpus_id=key_tag,policy_version,index_version,corpus_id

    def build(self,query, evidence):
        if query.identity_unresolved:
            raise ContractError('CLARIFY_UNRESOLVED_IDENTITY')
        validate_receipt(query.preparation,query.approval,key_tag=self.key_tag,policy_version=self.policy_version,
                         input_kind='query',corpus_id=self.corpus_id)
        if query.lookup_only and len({e for row in evidence if row.token_match_count or 'NAME_LOOKUP_CANDIDATE_ONLY' in row.evidence_reason_codes for e in row.entity_ids})>1:
            raise ContractError('CLARIFY_AMBIGUOUS_IDENTITY_CANDIDATES')
        if not 1<=len(evidence)<=5:
            raise ContractError('CONTEXT_EVIDENCE_COUNT')
        self.guard.check(query.preparation.sanitized_text)
        documents=[];seen=set()
        for row in evidence:
            if (not isinstance(row,RetrievedEvidence) or not DOCID.fullmatch(row.doc_id)
                    or row.doc_id in seen or row.identity_match=='conflict'
                    or row.approval_state not in {'HUMAN_APPROVED','AUTO_APPROVED'}
                    or row.key_tag!=self.key_tag or row.policy_version!=self.policy_version
                    or row.index_version!=self.index_version):
                raise ContractError('CONTEXT_UNAPPROVED_OR_VERSION_MISMATCH')
            self.guard.check(row.masked_text)
            seen.add(row.doc_id)
            documents.append({'doc_id':row.doc_id,'text':row.masked_text,
                              'verified_region_match':row.region_match})
        try:
            question,docs,_=validate_outbound(query.preparation.sanitized_text,documents)
        except Exception:
            raise ContractError('OUTBOUND_VALIDATION_FAILED') from None
        # Check bounded payload again: clipping must not split protection tokens.
        self.guard.check(question)
        for d in docs:
            if d['text'].count('<')!=d['text'].count('>'):
                raise ContractError('OUTBOUND_TRUNCATED_TOKEN')
            self.guard.check(d['text'])
        tokens=set(TOKEN.findall(question))
        for d in docs:tokens.update(TOKEN.findall(d['text']))
        return Context(question,tuple(docs),frozenset(tokens),self.index_version,bool(query.regions))

@dataclass(repr=False)
class MockTransport:
    response: dict = field(repr=False)
    calls: int = 0

    def complete(self,payload):
        if payload.get('model')!='HCX-007' or payload.get('temperature')!=0:
            raise ContractError('INVALID_MOCK_REQUEST')
        self.calls+=1
        return copy.deepcopy(self.response)

class NetworkBlockedTransport:
    def complete(self,payload):
        raise ContractError('NETWORK_NOT_RUN_BY_POLICY')

@dataclass(frozen=True, repr=False)
class GenerationResult:
    decision: str
    answer: str
    citations: tuple[str,...]
    support_evidence: tuple[dict,...]
    mode: str = 'MOCK_PASS'
    claim_support_status: str = 'CITATION_IDS_ONLY_NOT_FACTUAL_VERIFICATION'

class MockGenerator:
    def __init__(self,builder,transport):
        # Reject arbitrary caller transports: cannot enable a live API through config.
        if type(transport) is not MockTransport:
            raise ContractError('LIVE_TRANSPORT_FORBIDDEN')
        self.builder,self.transport=builder,transport

    def generate(self,query,evidence):
        context=self.builder.build(query,evidence)
        payload={'model':'HCX-007','temperature':0,'max_completion_tokens':1024,'_has_region':context.has_region,
                 'response_format':{'type':'json_schema','json_schema':{'name':'aegis_unified_v1','schema':copy.deepcopy(SCHEMA)}},
                 'messages':[{'role':'system','content':SYSTEM},
                             {'role':'user','content':json.dumps({'question':context.question,
                                                                'documents':list(context.documents)},ensure_ascii=False)}]}
        try:
            raw=self.transport.complete(payload)
            response=validate_response(raw,list(context.documents),context.has_region)
        except Exception:
            raise ContractError('GENERATION_OR_SCHEMA_FAILED') from None
        self.builder.guard.check(response['answer'],allowed_tokens=context.allowed_tokens)
        # Store the exact provided sanitized evidence range, not an invented claim verdict.
        support=tuple({'doc_id':d['doc_id'],'sanitized_start':0,'sanitized_end':len(d['text']),
                       'support_kind':'PROVIDED_CONTEXT_RANGE'} for d in context.documents if d['doc_id'] in response['citations'])
        return GenerationResult(response['decision'],response['answer'],tuple(response['citations']),support)

@dataclass(frozen=True,repr=False)
class LivePermit:
    """Explicit future synthetic-only authorization, exact context and reserved budget."""
    context_fingerprint:str
    max_calls:int=0
    total_budget:object=0
    reserved_cost_per_call:object=0
    enabled:bool=False
    dataset_role:str='synthetic'

    def validate(self):
        from decimal import Decimal,InvalidOperation
        try:
            budget=Decimal(str(self.total_budget));reserve=Decimal(str(self.reserved_cost_per_call))
        except (InvalidOperation,ValueError):raise ContractError('INVALID_LIVE_BUDGET') from None
        if (self.enabled is not True or self.dataset_role!='synthetic' or type(self.max_calls) is not int
                or self.max_calls<1 or not budget.is_finite() or not reserve.is_finite()
                or reserve<=0 or budget<reserve or not re.fullmatch(r'[0-9a-f]{64}',self.context_fingerprint)):
            raise ContractError('LIVE_OPT_IN_AND_SYNTHETIC_BUDGET_REQUIRED')
        return budget,reserve

class LiveTransport:
    """Thin delegation to frozen hcx.call_hcx. No key copying/global-env mutation.

    Off by default. Calling code must supply an authorized synthetic context hash,
    a conservative cost reservation per attempt, and the existing V6 environment
    opt-in. Provider price calculation/billing is not inferred here.
    """
    def __init__(self,env_file,guard, *, permit=None):
        from pathlib import Path
        import threading
        env_file=Path(env_file)
        if not env_file.is_absolute() or env_file.name!='.env' or not env_file.is_file():
            raise ContractError('EXISTING_ENV_FILE_REQUIRED')
        self._root=env_file.parent;self.guard=guard;self.permit=permit
        self.calls=0;self.reserved_cost=__import__('decimal').Decimal('0')
        self._lock=threading.Lock()

    def complete(self,payload):
        from .contracts import config_hash
        import os
        import hcx
        with self._lock:
            if self.permit is None:raise ContractError('LIVE_OPT_IN_REQUIRED')
            budget,reserve=self.permit.validate()
            if os.environ.get('AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED')!='YES':
                raise ContractError('EXTERNAL_TRANSFER_NOT_APPROVED')
            if payload.get('model')!='HCX-007' or payload.get('temperature')!=0:
                raise ContractError('INVALID_LIVE_MODEL_CONFIGURATION')
            try:
                content=json.loads(payload['messages'][-1]['content'])
                question=content['question'];documents=content['documents']
            except Exception:raise ContractError('INVALID_LIVE_CONTEXT') from None
            if self.permit.context_fingerprint!=config_hash(content):raise ContractError('UNAPPROVED_LIVE_CONTEXT')
            self.guard.check(question)
            for doc in documents:self.guard.check(doc['text'])
            try:question,documents,_=validate_outbound(question,documents)
            except Exception:raise ContractError('OUTBOUND_VALIDATION_FAILED') from None
            if self.calls>=self.permit.max_calls or self.reserved_cost+reserve>budget:
                raise ContractError('LIVE_CALL_OR_COST_LIMIT')
            # Failed attempts reserve their allowance too. No implicit retry.
            self.calls+=1;self.reserved_cost+=reserve
            try:
                response,usage,_=hcx.call_hcx(self._root,question,documents,bool(payload.get('_has_region')))
            except Exception:raise ContractError('HCX_TRANSPORT_FAILED') from None
            return response


class GuardedGenerator(MockGenerator):
    """Mock and explicitly gated live transports share outbound/output validation."""
    def __init__(self,builder,transport):
        if type(transport) not in {MockTransport,LiveTransport}:raise ContractError('UNSUPPORTED_TRANSPORT')
        self.builder,self.transport=builder,transport

    def generate(self,query,evidence):
        from dataclasses import replace
        if type(self.transport) is LiveTransport:
            if not (self.builder.corpus_id=='synthetic' or self.builder.corpus_id.startswith('synthetic_')):
                raise ContractError('PRIVATE_CORPUS_EXTERNAL_TRANSFER_FORBIDDEN')
            if any(row.index_version.startswith('legacy_') for row in evidence):
                raise ContractError('LEGACY_PRIVATE_INDEX_EXTERNAL_TRANSFER_FORBIDDEN')
        result=super().generate(query,evidence)
        return replace(result,mode='LIVE_VALIDATED') if type(self.transport) is LiveTransport else result
