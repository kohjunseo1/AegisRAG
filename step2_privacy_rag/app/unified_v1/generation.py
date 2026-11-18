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
        payload={'model':'HCX-007','temperature':0,'max_completion_tokens':1024,
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
