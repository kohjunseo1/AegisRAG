"""Opt-in approved experiment index; common-corpus algorithm comparisons only."""
from __future__ import annotations
from dataclasses import dataclass, replace
import math
import re
from typing import Protocol
from metrics_retrieval import summarize_score
from .contracts import ContractError, PreparationResult, RetrievedEvidence, fingerprint, config_hash

TOKEN = re.compile(r'<(?:PERSON|PHONE_NUMBER|EMAIL|ADDRESS|RESIDENT_REGISTRATION_NUMBER|ACCOUNT_NUMBER|CARD_NUMBER)_[0-9a-f]{24}>')
REGION = re.compile(r'region_v1_[0-9a-f]{24}\Z')
ENTITY = re.compile(r'e_[0-9a-f]{24}\Z')
NAME_LOOKUP = re.compile(r'n_[0-9a-f]{24}\Z')
DOCID = re.compile(r'[A-Za-z0-9_-]{1,60}\Z')

@dataclass(frozen=True, repr=False)
class ExperimentApproval:
    corpus_id: str
    sanitized_fingerprint: str
    key_tag: str
    policy_version: str
    input_kind: str
    allow_mask_only: bool = False

def approve_experiment(prepared, *, corpus_id, checker, allow_mask_only=False):
    """Explicit local corpus approval, never an ACL or production approval.

    Independent checker must raise or return False on uncertainty/failure.
    No automatic masked-only path; caller must opt into an experimental approval.
    """
    if (prepared.state != 'LOCAL_PREPARED' or not prepared.coverage_verified
            or prepared.sanitized_text is None or prepared.protection_kind == 'NONE'
            or not DOCID.fullmatch(corpus_id)
            or (prepared.protection_kind == 'MASKED_ONLY' and not allow_mask_only)):
        raise ContractError('EXPERIMENT_APPROVAL_REFUSED')
    try:
        checked=checker(prepared.sanitized_text)
    except Exception:
        raise ContractError('INDEPENDENT_CHECK_FAILED') from None
    if checked is not True:
        raise ContractError('INDEPENDENT_CHECK_FAILED')
    approved=replace(prepared,approval_state='HUMAN_APPROVED')
    receipt=ExperimentApproval(corpus_id,fingerprint(prepared.sanitized_text),prepared.key_tag,
                               prepared.policy_version,prepared.input_kind,allow_mask_only)
    return approved,receipt

def validate_receipt(prepared, receipt, *, key_tag, policy_version, input_kind, corpus_id):
    prepared.require_approved(key_tag=key_tag,policy_version=policy_version,input_kind=input_kind)
    if (receipt.corpus_id != corpus_id or receipt.sanitized_fingerprint != fingerprint(prepared.sanitized_text)
            or receipt.key_tag != key_tag or receipt.policy_version != policy_version
            or receipt.input_kind != input_kind
            or (prepared.protection_kind == 'MASKED_ONLY' and not receipt.allow_mask_only)):
        raise ContractError('APPROVAL_RECEIPT_MISMATCH')

@dataclass(frozen=True, repr=False)
class IndexedDocument:
    doc_id: str
    preparation: PreparationResult
    approval: ExperimentApproval
    regions: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    chunk_id: str | None = None
    name_lookup_keys: tuple[str, ...] = ()

@dataclass(frozen=True, repr=False)
class QuerySpec:
    preparation: PreparationResult
    approval: ExperimentApproval
    regions: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    identity_unresolved: bool = False
    lookup_only: bool = False
    name_lookup_keys: tuple[str, ...] = ()

class DenseScorer(Protocol):
    def __call__(self, masked_query: str, documents: tuple[IndexedDocument,...]) -> dict[str,float]: ...

class InMemoryIndex:
    def __init__(self, *, key_tag, policy_version, corpus_id, index_version='unified_synthetic_v1'):
        self.key_tag,self.policy_version,self.corpus_id=key_tag,policy_version,corpus_id
        self.index_version=index_version
        self._docs={}

    def _metadata(self,regions,entities,name_lookup_keys=()):
        if any(not REGION.fullmatch(r) for r in regions) or any(not ENTITY.fullmatch(e) for e in entities):
            raise ContractError('INVALID_REGION_OR_ENTITY_KEY')
        if any(not NAME_LOOKUP.fullmatch(k) for k in name_lookup_keys):raise ContractError('INVALID_NAME_LOOKUP_KEY')

    def add(self,doc: IndexedDocument):
        if not DOCID.fullmatch(doc.doc_id) or doc.chunk_id is not None:
            raise ContractError('INVALID_DOCUMENT_ID_OR_UNSUPPORTED_CHUNK_MODE')
        self._metadata(doc.regions,doc.entity_ids,doc.name_lookup_keys)
        validate_receipt(doc.preparation,doc.approval,key_tag=self.key_tag,policy_version=self.policy_version,
                         input_kind='document',corpus_id=self.corpus_id)
        if doc.doc_id in self._docs and self._docs[doc.doc_id] != doc:
            raise ContractError('INSERT_ONCE_CONFLICT')
        self._docs[doc.doc_id]=doc

    @property
    def ids(self):
        return frozenset(self._docs)

    @property
    def index_fingerprint(self):
        return config_hash({'index_version':self.index_version,'key_tag':self.key_tag,
                            'policy':self.policy_version,'corpus':self.corpus_id,
                            'docs':[(did,fingerprint(d.preparation.sanitized_text),d.regions,d.entity_ids,d.name_lookup_keys)
                                    for did,d in sorted(self._docs.items())]})

    def retrieve(self,query: QuerySpec, *, profile='hybrid_v1', top_k=5, dense_scorer=None):
        if profile not in {'own_v6','team_v3','hybrid_v1'} or type(top_k) is not int or not 1<=top_k<=10:
            raise ContractError('INVALID_RETRIEVAL_PROFILE_OR_LIMIT')
        if query.identity_unresolved:
            raise ContractError('CLARIFY_UNRESOLVED_IDENTITY')
        self._metadata(query.regions,query.entity_ids,query.name_lookup_keys)
        validate_receipt(query.preparation,query.approval,key_tag=self.key_tag,policy_version=self.policy_version,
                         input_kind='query',corpus_id=self.corpus_id)
        qtext=query.preparation.sanitized_text
        qtokens=set(TOKEN.findall(qtext))
        docs=tuple(self._docs.values())
        if dense_scorer is None:
            scores={}
        else:
            try:
                scores=dense_scorer(qtext,docs)
                if not isinstance(scores,dict) or not set(scores)<=self.ids:
                    raise ValueError
                if any(type(s) not in {int,float} or not math.isfinite(s) for s in scores.values()):
                    raise ValueError
            except Exception:
                raise ContractError('DENSE_SCORING_FAILED') from None
        return rank_documents(query,docs,scores,profile=profile,top_k=top_k,index_version=self.index_version,
                              key_tag=self.key_tag,policy_version=self.policy_version)

def rank_documents(query, docs, scores, *, profile,top_k,index_version,key_tag,policy_version,dense_pool_limit=None):
    if profile not in {'own_v6','team_v3','hybrid_v1'} or type(top_k) is not int or not 1<=top_k<=10:
        raise ContractError('INVALID_RETRIEVAL_PROFILE_OR_LIMIT')
    qtokens=set(TOKEN.findall(query.preparation.sanitized_text))
    dense_sorted=sorted(scores,key=lambda did:(-scores[did],did))
    dense_rank={did:i for i,did in enumerate(dense_sorted,1)}
    dense_pool=set(dense_sorted[:dense_pool_limit or max(50,top_k*3)])
    pool=[]
    for doc in docs:
        tokens=set(TOKEN.findall(doc.preparation.sanitized_text))
        count=len(qtokens & tokens)
        region_count=len(set(query.regions)&set(doc.regions))
        region=bool(region_count)
        allhit=bool(qtokens) and qtokens<=tokens
        identity=('exact' if set(query.entity_ids)&set(doc.entity_ids) else
                  'conflict' if query.entity_ids and doc.entity_ids else
                  'weak' if count else 'none')
        # Explicit entity conflicts are excluded from every comparison profile.
        if identity=='conflict':continue
        lookup=bool(set(query.name_lookup_keys)&set(doc.name_lookup_keys))
        if not (count or region or doc.doc_id in dense_pool or identity=='exact' or lookup):continue
        if lookup and identity=='none':identity='weak'
        reasons=[]
        if lookup:reasons.append('NAME_LOOKUP_CANDIDATE_ONLY')
        if allhit:reasons.append('TOKEN_ALL')
        if count:reasons.append('TOKEN_ANY')
        if region:reasons.append('REGION')
        if doc.doc_id in dense_pool:reasons.append('DENSE')
        if identity=='exact':reasons.append('EXACT_ENTITY')
        score=scores.get(doc.doc_id,float('-inf'))
        if profile=='own_v6':
            # V6 semantic baseline: dense candidate pool, token->region->dense sort.
            if doc.doc_id not in dense_pool:continue
            order=(-count,-region_count,-score,doc.doc_id)
        elif profile=='team_v3':
            group=0 if allhit and query.regions and set(query.regions)<=set(doc.regions) else 1 if allhit else 2 if count or lookup else 3 if region else 4
            order=(group,-score,doc.doc_id)
        else:
            order=(-int(identity=='exact'),-int(lookup),-count,-int(region),-score,doc.doc_id)
        row=RetrievedEvidence(doc.doc_id,doc.preparation.sanitized_text,identity,count,region,
                              dense_rank.get(doc.doc_id,0),tuple(reasons),index_version,
                              key_tag,policy_version,doc.preparation.approval_state,entity_ids=doc.entity_ids)
        pool.append((order,row))
    return tuple(row for _,row in sorted(pool,key=lambda x:x[0])[:top_k])



def evaluate_rankings(index, queries, gold, rankings, query_states, *, dataset_role='synthetic',real_model_metrics=False):
    """Use frozen scorer; retain full-set and eligible denominators and actual IDs.

    Queries are opaque qids + readiness marker only, no question text is exported.
    """
    if any(s not in {'READY','REVIEW','BLOCKED'} for s in query_states.values()):
        raise ContractError('INVALID_QUERY_GATE_STATE')
    if set(query_states)!={q['qid'] for q in queries}:
        raise ContractError('QUERY_STATE_MISMATCH')
    safe_queries=[{'qid':q['qid'],'text':'APPROVED' if query_states[q['qid']]=='READY' else None} for q in queries]
    result=summarize_score(safe_queries,gold,set(index.ids),rankings)
    result.update(dataset_role=dataset_role,neutral_policy='scoring_only; actual top5 unchanged',
                  query_gate_counts={s:sum(v==s for v in query_states.values()) for s in ['READY','REVIEW','BLOCKED']},
                  actual_top5={q['qid']:rankings.get(q['qid'],[])[:5] for q in queries},
                  metrics_definition_version='v6_common_v1',index_fingerprint=index.index_fingerprint,
                  real_model_metrics=real_model_metrics)
    return result
