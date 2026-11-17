"""Local-only generic dataset staging and fail-closed manual correction.

A reviewer supplies corrected *source text*, which is reprocessed by the same
detector and Privacy Gate. Approval is impossible by merely toggling a flag.
No external LLM calls or DB writes occur in this module.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile

from document_preparation import prepare
from dataset_preparation import key_tag
from hcx import redact_residual_pii
from live_index import DOC_ID

FORMAT = 'aegisrag-dataset-stage-v1'
BASE = Path(__file__).resolve().parents[1] / 'local_eval'
DATASET = re.compile(r'[a-z][a-z0-9_-]{1,22}\Z')
FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def _mac(payload: dict, key: bytes) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hmac.new(key, b'aegisrag:stage:v1\0' + raw, hashlib.sha256).hexdigest()


def secure_path(path: Path) -> Path:
    result = path.expanduser().resolve()
    if not result.is_relative_to(BASE.resolve()) or result.suffix != '.json':
        raise ValueError('PRIVATE_LOCAL_EVAL_JSON_REQUIRED')
    return result


def write_private(path: Path, payload: dict, replace: bool = False):
    path = secure_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists() and not replace:
        raise ValueError('PRIVATE_OUTPUT_EXISTS')
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp_stage_')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        if path.exists() and not replace:
            raise ValueError('PRIVATE_OUTPUT_EXISTS')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def signed(stage: dict, key: bytes) -> dict:
    return {**stage, 'signature': _mac(stage, key)}


def read_signed(path: Path, key: bytes) -> dict:
    try:
        obj = json.loads(secure_path(path).read_text(encoding='utf-8'))
    except Exception:
        raise ValueError('INVALID_STAGE_FILE') from None
    if not isinstance(obj, dict):
        raise ValueError('INVALID_STAGE_FILE')
    signature = obj.pop('signature', None)
    if not isinstance(signature, str) or not hmac.compare_digest(signature, _mac(obj, key)):
        raise ValueError('INVALID_STAGE_SIGNATURE')
    if obj.get('format') != FORMAT or obj.get('hmac_key_tag') != key_tag(key):
        raise ValueError('STAGE_KEY_OR_FORMAT_MISMATCH')
    if not isinstance(obj.get('documents'), list):
        raise ValueError('INVALID_STAGE_DOCUMENTS')
    return obj


def doc_id(dataset: str, src: str, key: bytes) -> str:
    """Opaque stable ID; never expose an untrusted original document ID to DB/chat.

    Domain-separated HMAC avoids raw names/phone numbers hidden inside source IDs
    leaking via document citations, logs or SQL IDs.
    """
    if not isinstance(dataset, str) or not DATASET.fullmatch(dataset):
        raise ValueError('INVALID_DATASET_NAMESPACE')
    if not isinstance(src, str) or not src.strip() or not isinstance(key, bytes) or len(key)<32:
        raise ValueError('INVALID_SOURCE_ID_OR_KEY')
    digest=hmac.new(key, b'aegisrag:document-id:v1\0'+
                   (dataset+'\0'+src).encode('utf-8'), hashlib.sha256).hexdigest()
    return dataset + '__' + digest[:24]


def input_rows(path: Path, *, id_field='id', text_field='text',
               title_field=None, type_field=None) -> list[tuple[str,str]]:
    if any(not FIELD.fullmatch(f) for f in (id_field,text_field)):
        raise ValueError('INVALID_INPUT_FIELD')
    if any(f is not None and not FIELD.fullmatch(f) for f in (title_field,type_field)):
        raise ValueError('INVALID_INPUT_FIELD')
    if not path.is_file() or path.suffix.lower() != '.jsonl':
        raise ValueError('JSONL_INPUT_REQUIRED')
    if path.stat().st_size > 50_000_000:
        raise ValueError('INPUT_TOO_LARGE')
    rows, seen = [], set()
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError('INVALID_INPUT_ROW')
            src = item.get(id_field)
            if type(src) is int:
                src = str(src)
            body = item.get(text_field)
            if not isinstance(src, str) or not src.strip() or not isinstance(body,str) or not body.strip():
                raise ValueError('INPUT_ID_TEXT_REQUIRED')
            if src in seen:
                raise ValueError('DUPLICATE_INPUT_ID')
            seen.add(src)
            parts=[]
            for label, field in (('문서 유형',type_field),('제목',title_field)):
                if field and item.get(field) is not None:
                    val=item[field]
                    if not isinstance(val,str) or len(val)>300:
                        raise ValueError('INVALID_OPTIONAL_METADATA')
                    if val.strip():
                        parts.append(label+': '+val.strip())
            parts.append(body.strip())
            source='\n'.join(parts)
            if len(source)>100000:
                raise ValueError('DOCUMENT_TOO_LARGE')
            rows.append((src,source))
            if len(rows)>1000:
                raise ValueError('BATCH_LIMIT_1000')
    except (json.JSONDecodeError,UnicodeError):
        raise ValueError('INVALID_JSONL_CONTENT') from None
    if not rows:
        raise ValueError('EMPTY_INPUT')
    return rows


def _process(src: str, did: str, raw: str, detector, key: bytes) -> dict:
    out = prepare(did, raw, detector, key)
    row = dict(doc_id=did, source_id=src, source_sha256=_hash(raw),
               state='REVIEW_PENDING', reason=out.reason,
               approval_state=None, text=None, region_keys=[],
               raw_for_local_review=raw)
    if (out.state=='LOCAL_PREPARED' and out.unlinked_mentions==0
            and isinstance(out.sanitized_text,str) and out.sanitized_text.strip()):
        _, residues = redact_residual_pii(out.sanitized_text)
        if not any(residues.values()):
            row.update(state='READY',reason='AUTO_GATE_READY',
                       approval_state='AUTO_READY', text=out.sanitized_text,
                       region_keys=list(out.region_keys),raw_for_local_review=None)
        else:
            row['reason']='RESIDUAL_PII_REQUIRES_REVIEW'
    elif out.state == 'BLOCKED':
        row['state']='BLOCKED'
    elif out.unlinked_mentions:
        row['reason']='UNLINKED_MENTIONS_REVIEW'
    return row


def stage_dataset(rows: list[tuple[str,str]], dataset: str, detector, key: bytes) -> dict:
    if not DATASET.fullmatch(dataset) or not 1<=len(rows)<=1000:
        raise ValueError('INVALID_DATASET_OR_BATCH_SIZE')
    result, ids=[],set()
    for src,raw in rows:
        did=doc_id(dataset,src,key)
        if did in ids:
            raise ValueError('COLLIDING_DOCUMENT_IDS')
        ids.add(did)
        result.append(_process(src,did,raw,detector,key))
    return dict(format=FORMAT,dataset=dataset,hmac_key_tag=key_tag(key),
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                documents=result)


def review_corrected(stage: dict, did: str, revised: str, reviewer: str,
                     detector, key: bytes) -> dict:
    if not isinstance(reviewer,str) or not re.fullmatch(r'[A-Za-z0-9_-]{2,40}',reviewer):
        raise ValueError('INVALID_REVIEWER')
    if not isinstance(revised,str) or not revised.strip():
        raise ValueError('INVALID_REVISED_TEXT')
    matches=[(i,r) for i,r in enumerate(stage['documents']) if r['doc_id']==did]
    if len(matches)!=1:
        raise ValueError('UNKNOWN_REVIEW_DOC_ID')
    idx,old=matches[0]
    if old['state'] not in ('REVIEW_PENDING','BLOCKED'):
        raise ValueError('DOC_NOT_PENDING_REVIEW')
    if _hash(revised)==old['source_sha256']:
        raise ValueError('REVIEW_REQUIRES_SOURCE_EDIT')
    candidate=_process(old['source_id'],did,revised,detector,key)
    if candidate['state']!='READY':
        raise ValueError('REVISED_TEXT_NOT_GATE_READY')
    candidate.update(approval_state='HUMAN_APPROVED',
                     source_sha256=old['source_sha256'],
                     revised_source_sha256=_hash(revised),
                     reviewer=reviewer,
                     approved_at_utc=datetime.now(timezone.utc).isoformat())
    stage['documents'][idx]=candidate
    return stage


def export_approved(stage: dict) -> dict:
    documents=[
        {'doc_id':d['doc_id'],'text':d['text'],'region_keys':d['region_keys'],
         'approval_state':d['approval_state']}
        for d in stage['documents']
        if d['state']=='READY' and d['approval_state'] in ('AUTO_READY','HUMAN_APPROVED')
    ]
    if not documents:
        raise ValueError('NO_APPROVED_DOCS')
    return dict(format='aegisrag-live-approved-v1',
                hmac_key_tag=stage['hmac_key_tag'],documents=documents)


def summary(stage: dict) -> dict:
    counts=Counter(d['state'] for d in stage['documents'])
    return dict(dataset=stage['dataset'],total=len(stage['documents']),
                ready=counts['READY'],review_pending=counts['REVIEW_PENDING'],
                blocked=counts['BLOCKED'],
                review_ids=[d['doc_id'] for d in stage['documents']
                            if d['state']!='READY'])
