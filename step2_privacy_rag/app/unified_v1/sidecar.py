"""Signed local metadata sidecar, never identity inference from name/role alone."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
from .contracts import ContractError,fingerprint
from .retrieval import TOKEN,ENTITY,NAME_LOOKUP,DOCID


def legacy_lookup_key(key,person_token):
    if not __import__('re').fullmatch(r'<PERSON_[0-9a-f]{24}>',person_token):raise ContractError('INVALID_PERSON_TOKEN')
    return 'n_'+hmac.new(key,b'aegisrag:legacy-name-lookup:v1\x00'+person_token.encode(),hashlib.sha256).hexdigest()[:24]


def _signature(payload,key):
    return hmac.new(key,b'aegisrag:identity-sidecar:v1\x00'+json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()


def create_sidecar(documents, *, index_fingerprint,key,key_tag,linked_mentions=(),verified_source_fingerprints=None):
    from dataset_preparation import key_tag as frozen_tag
    if type(key) is not bytes or len(key)<32 or key_tag!=frozen_tag(key):raise ContractError('SIDECAR_KEY_MISMATCH')
    verified_source_fingerprints=verified_source_fingerprints or {}
    by_doc={};linked_names={}
    for mention in linked_mentions:
        if mention.link_status=='LINKED' and mention.entity_id:
            by_doc.setdefault(mention.doc_id,set()).add(mention.entity_id)
            if mention.name_lookup_key:linked_names.setdefault(mention.doc_id,set()).add(mention.name_lookup_key)
    entries={}
    for row in documents:
        did,text=row['doc_id'],row['masked_text']
        if not DOCID.fullmatch(did):raise ContractError('INVALID_SIDECAR_DOC_ID')
        names=sorted({t for t in TOKEN.findall(text) if t.startswith('<PERSON_')})
        entities=sorted(by_doc.get(did,()))
        if entities and verified_source_fingerprints.get(did)!=fingerprint(text):
            raise ContractError('SIDECAR_SOURCE_PROVENANCE_REQUIRED')
        if any(not ENTITY.fullmatch(e) for e in entities):raise ContractError('INVALID_SIDECAR_ENTITY')
        entries[did]=dict(name_lookup_keys=[legacy_lookup_key(key,t) for t in names],entity_ids=entities,
                          unresolved_name_count=max(0,len(names)-len(linked_names.get(did,()))),masked_fingerprint=fingerprint(text))
    payload=dict(schema='identity_sidecar_v1',index_fingerprint=index_fingerprint,key_tag=key_tag,entries=entries,
                 identity_policy='anchor_conservative_v1',approval='LOCAL_METADATA_ONLY_NOT_EXTERNAL')
    return dict(payload=payload,signature=_signature(payload,key))


def validate_sidecar(sidecar, *, index_fingerprint,key,key_tag):
    try:
        payload=sidecar['payload']
        if (not hmac.compare_digest(sidecar['signature'],_signature(payload,key)) or payload['index_fingerprint']!=index_fingerprint
                or payload['key_tag']!=key_tag or payload['schema']!='identity_sidecar_v1'):
            raise ValueError
        for did,row in payload['entries'].items():
            if (not DOCID.fullmatch(did) or any(not ENTITY.fullmatch(e) for e in row['entity_ids'])
                    or any(not NAME_LOOKUP.fullmatch(k) for k in row['name_lookup_keys'])):raise ValueError
        return payload
    except Exception:raise ContractError('SIDECAR_SIGNATURE_OR_PROVENANCE_FAILED') from None


def save_sidecar(path,sidecar):
    path=Path(path)
    if 'private' not in path.parts or not path.is_absolute():raise ContractError('PRIVATE_SIDECAR_PATH_REQUIRED')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(sidecar,stream,sort_keys=True)
        os.replace(tmp,path);path.chmod(0o600)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def coverage(sidecar):
    rows=list(sidecar['payload']['entries'].values())
    names=sum(len(r['name_lookup_keys']) for r in rows)
    unresolved=sum(r['unresolved_name_count'] for r in rows)
    return dict(documents=len(rows),name_candidate_documents=sum(bool(r['name_lookup_keys']) for r in rows),
                unique_names_per_doc_total=names,entity_documents=sum(bool(r['entity_ids']) for r in rows),
                unresolved_names=unresolved,unresolved_rate=unresolved/names if names else None,
                source='LEGACY_TOKEN_LOOKUP_ONLY; NO_RAW_OWNERSHIP_INFERRED')
