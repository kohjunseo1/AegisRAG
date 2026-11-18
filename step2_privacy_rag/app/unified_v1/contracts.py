"""Immutable, PII-safe contracts. Unicode codepoint [start,end) offsets only."""
from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType

LABELS = frozenset({'person', 'phone_number', 'email', 'address',
                    'resident_registration_number', 'account_number', 'card_number'})
SOURCES = frozenset({'ax', 'veil', 'klue', 'koelectra', 'structural', 'contextual'})
PROFILES = {'own2': ('ax', 'veil'), 'team2': ('klue', 'koelectra'),
            'quality4': ('ax', 'veil', 'klue', 'koelectra')}
for _source in PROFILES['quality4']:
    PROFILES['drop_' + _source] = tuple(s for s in PROFILES['quality4'] if s != _source)

class ContractError(ValueError):
    """Only constant error codes, never raw input or library exception details."""

def fingerprint(text: str) -> str:
    if not isinstance(text, str):
        raise ContractError('INVALID_TEXT')
    return sha256(text.encode('utf8')).hexdigest()

def config_hash(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True, repr=False)
class Candidate:
    source_id: str
    source_revision: str
    label: str
    start: int
    end: int
    original_input_fingerprint: str
    offset_unit: str = 'unicode_codepoint'
    confidence_raw: float | None = None
    confidence_semantics: str | None = None
    evidence_codes: tuple[str, ...] = ()
    window_id: str | None = None

    def validate(self, text: str):
        if self.source_id not in SOURCES or not self.source_revision or self.label not in LABELS:
            raise ContractError('INVALID_CANDIDATE_SOURCE_OR_LABEL')
        if (type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end <= len(text)
                or self.offset_unit != 'unicode_codepoint'):
            raise ContractError('INVALID_OFFSET')
        if self.original_input_fingerprint != fingerprint(text):
            raise ContractError('INPUT_FINGERPRINT_MISMATCH')
        if self.confidence_raw is not None and (not math.isfinite(self.confidence_raw)
                                                or not self.confidence_semantics):
            raise ContractError('INVALID_CONFIDENCE')
        return self

@dataclass(frozen=True, repr=False)
class DetectionEvidence:
    input_kind: str
    request_id: str
    raw_candidates_by_source: dict[str, tuple[Candidate, ...]]
    model_status_by_source: dict[str, str]
    structural_candidates: tuple[Candidate, ...]
    config_hash: str
    original_input_fingerprint: str
    detector_profile: str

    def __post_init__(self):
        required = set(PROFILES.get(self.detector_profile, ()))
        if (not required or self.input_kind not in {"document", "query"}
                or not self.request_id or not re.fullmatch(r"[0-9a-f]{64}", self.config_hash)
                or set(self.raw_candidates_by_source) != required
                or set(self.model_status_by_source) != required
                or any(s not in {"OK", "ERROR", "TIMEOUT", "UNAVAILABLE"} for s in self.model_status_by_source.values())):
            raise ContractError("INVALID_DETECTION_EVIDENCE")
        raw = {s: tuple(rows) for s, rows in self.raw_candidates_by_source.items()}
        if any(c.source_id != s or c.original_input_fingerprint != self.original_input_fingerprint
               for s, rows in raw.items() for c in rows):
            raise ContractError("EVIDENCE_SOURCE_MISMATCH")
        object.__setattr__(self, "raw_candidates_by_source", MappingProxyType(raw))
        object.__setattr__(self, "model_status_by_source", MappingProxyType(dict(self.model_status_by_source)))
        object.__setattr__(self, "structural_candidates", tuple(self.structural_candidates))

    @property
    def ready(self):
        required = PROFILES.get(self.detector_profile, ())
        return bool(required) and all(self.model_status_by_source.get(s) == 'OK' for s in required)

@dataclass(frozen=True, repr=False)
class ProtectionPlan:
    strict_entities: tuple[Candidate, ...]
    entities_for_hmac: tuple[Candidate, ...]
    mask_intervals: tuple[tuple[int, int], ...]
    redaction_intervals: tuple[tuple[int, int], ...]
    unresolved_mentions: tuple[Candidate, ...]
    review_reason_codes: tuple[str, ...]
    detector_profile: str
    policy_version: str = 'evidence_v1'
    original_input_fingerprint: str = ''
    input_kind: str = 'document'

@dataclass(frozen=True, repr=False)
class PreparationResult:
    state: str
    protection_kind: str
    approval_state: str
    sanitized_text: str | None = field(repr=False)
    key_tag: str
    policy_version: str
    input_kind: str
    reason_codes: tuple[str, ...] = ()
    coverage_verified: bool = False

    def require_approved(self, *, key_tag: str, policy_version: str, input_kind: str):
        if (self.state != 'LOCAL_PREPARED' or not self.coverage_verified
                or self.approval_state not in {'AUTO_APPROVED', 'HUMAN_APPROVED'}
                or self.sanitized_text is None or self.protection_kind == 'NONE'
                or self.key_tag != key_tag or self.policy_version != policy_version
                or self.input_kind != input_kind):
            raise ContractError('PREPARATION_NOT_APPROVED_OR_VERSION_MISMATCH')

@dataclass(frozen=True, repr=False)
class PersonMention:
    mention_id: str
    doc_id: str
    name_lookup_key: str | None
    attribute_key_ids: tuple[str, ...]
    relation_evidence_codes: tuple[str, ...]
    entity_id: str | None = None
    link_status: str = 'UNRESOLVED'

@dataclass(frozen=True, repr=False)
class RetrievedEvidence:
    doc_id: str
    masked_text: str
    identity_match: str
    token_match_count: int
    region_match: bool
    dense_rank: int
    evidence_reason_codes: tuple[str, ...]
    index_version: str
    key_tag: str
    policy_version: str
    approval_state: str
    chunk_id: str | None = None

@dataclass(frozen=True)
class Config:
    detector_profile: str = 'own2'
    retrieval_profile: str = 'hybrid_v1'
    mode: str = 'dry_run'
    input_kind: str = 'document'
    hmac_bits: int = 96
    index_mask_only: bool = False
    api_live: bool = False
    top_k: int = 5

    def __post_init__(self):
        if (self.detector_profile not in PROFILES
                or self.retrieval_profile not in {'own_v6', 'team_v3', 'hybrid_v1'}
                or self.mode not in {'dry_run', 'candidate_cache_replay', 'live_local'}
                or self.input_kind not in {'document', 'query'} or self.hmac_bits != 96
                or self.index_mask_only or self.api_live or type(self.top_k) is not int
                or not 1 <= self.top_k <= 5):
            raise ContractError('UNSUPPORTED_CONFIGURATION')
