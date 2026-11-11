#!/usr/bin/env python3
"""Local-only V5 pseudonymization into private sanitized eval format. Never print PII."""
from __future__ import annotations
import argparse, base64, hashlib, hmac, json, os, re, stat, sys, tempfile, zipfile
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
STEP2 = ROOT / 'step2_privacy_rag'
E2E = STEP2 / 'app'
ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
DEFAULT_DIR=STEP2/'local_eval'/'koe5_v5_private'


def _dotenv(path: Path):
    if not path.is_file(): return {}
    from dotenv import dotenv_values
    return dotenv_values(path)


def fixed_key(env_file: Path) -> bytes:
    cfg = _dotenv(env_file)
    # Hex is compatible with the prior team .env/HMAC_KEY workflow.
    hx = os.environ.get('AEGISRAG_HMAC_KEY_HEX') or cfg.get('AEGISRAG_HMAC_KEY_HEX') or cfg.get('HMAC_KEY')
    b64 = os.environ.get('AEGISRAG_HMAC_KEY_B64') or cfg.get('AEGISRAG_HMAC_KEY_B64')
    if hx:
        try: k=bytes.fromhex(hx)
        except ValueError: raise ValueError('invalid_fixed_hmac_key_hex') from None
    elif b64:
        try: k=base64.b64decode(b64, validate=True)
        except Exception: raise ValueError('invalid_fixed_hmac_key_b64') from None
    else:
        raise ValueError('fixed_key_missing (use .env HMAC_KEY hex or AEGISRAG_HMAC_KEY_HEX)')
    if len(k)<32: raise ValueError('fixed_key_too_short')
    return k


def key_tag(key: bytes) -> str:
    # Non-secret 16-character version identifier; NEVER an HMAC token for an entity.
    return hmac.new(key,b'aegisrag-koe5-v5-experiment-key-check-v1',hashlib.sha256).hexdigest()[:16]


def load_jsonl(path: Path):
    if not path.is_file(): raise ValueError('missing_jsonl_file')
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def load_gold(zippath: Path):
    if not zippath.is_file():raise ValueError('starter_zip_missing')
    with zipfile.ZipFile(zippath) as z:
        matches=[n for n in z.namelist() if n.endswith('/data/holdout_retrieval_queries_gold.jsonl')]
        if len(matches)!=1:raise ValueError('invalid_starter_zip_gold')
        return [json.loads(line) for line in z.read(matches[0]).decode('utf-8').splitlines() if line.strip()]


def private_write(path: Path, obj: dict):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent,0o700)
    fd,tmp=tempfile.mkstemp(prefix='.aegis_',suffix='.json',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
        os.chmod(path,0o600)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docs',type=Path,default=STEP2/'local_eval'/'holdout_labeled.jsonl')
    p.add_argument('--queries',type=Path,default=STEP2/'local_eval'/'holdout_retrieval_queries.jsonl')
    p.add_argument('--starter-zip',type=Path,default=None,help='Gold: scoring only; never an input to detector or masking')
    p.add_argument('--gold-jsonl',type=Path,default=None,help='Alternatively, local holdout_retrieval_queries_gold.jsonl')
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--out',type=Path,default=STEP2/'local_eval'/'koe5_v6_private'/'sanitized_eval.json')
    p.add_argument('--overwrite',action='store_true')
    a=p.parse_args(argv)
    if not (ROOT/'step1_pii_detector/src/pii_detector/detector.py').is_file():p.error('run_from_AegisRAG_root')
    if not (E2E/'document_preparation.py').is_file():p.error('missing_e2e_local_v1_dependency')
    if a.out.exists():p.error('v6_refuses_to_overwrite_existing_private_file')
    if not (a.docs.is_file() and a.queries.is_file()):p.error('missing_input: provide --docs and --queries (two JSONL files)')
    try:key=fixed_key(a.env)
    except ValueError as ex:p.error(str(ex))
    sys.path.insert(0,str((ROOT/'step1_pii_detector/src').resolve()))
    sys.path.insert(0,str(E2E.resolve()))
    from pii_detector import create_detector
    from pii_detector import detector as step1_mod
    if Path(step1_mod.__file__).resolve() != (ROOT/'step1_pii_detector/src/pii_detector/detector.py').resolve():p.error('unexpected_pii_detector_module')
    from document_preparation import prepare
    if bool(a.starter_zip) == bool(a.gold_jsonl):p.error('specify_exactly_one_of_--starter-zip_or_--gold-jsonl')
    docs_in=load_jsonl(a.docs); queries_in=load_jsonl(a.queries)
    gold_in=load_gold(a.starter_zip) if a.starter_zip else load_jsonl(a.gold_jsonl)
    if len(docs_in)!=187 or len(queries_in)!=88 or len(gold_in)!=88:p.error('holdout_dataset_count_mismatch')
    qids={q['qid'] for q in queries_in}; gids={g['qid'] for g in gold_in}
    if qids!=gids or len(qids)!=88:p.error('gold_query_id_mismatch')
    docs,queries,dc,qc=[],[],Counter(),Counter()
    try:
        with create_detector() as detector:
            for d in docs_in:
                ident=d.get('id'); txt=d.get('text')
                if not isinstance(ident,str) or not ID.fullmatch(ident) or not isinstance(txt,str):raise ValueError('invalid_document_input')
                r=prepare(ident,txt,detector,key);dc[r.state]+=1
                if r.state=='LOCAL_PREPARED':docs.append({'doc_id':ident,'text':r.sanitized_text,'region_keys':list(r.region_keys)})
            for q in queries_in:
                ident=q.get('qid'); txt=q.get('query')
                if not isinstance(ident,str) or not ID.fullmatch(ident) or not isinstance(txt,str):raise ValueError('invalid_query_input')
                r=prepare(ident,txt,detector,key);qc[r.state]+=1
                # Queries with unresolved identity cannot honestly claim exact HMAC token match.
                eligible = r.state=='LOCAL_PREPARED' and not r.unlinked_mentions
                queries.append({'qid':ident,'text':r.sanitized_text if eligible else None,
                                'state':'READY' if eligible else 'DEFERRED','region_keys':list(r.region_keys) if eligible else []})
                if r.state=='LOCAL_PREPARED' and r.unlinked_mentions:qc['UNLINKED_QUERY_DEFERRED']+=1
    except Exception:
        print('PRIVATE_PREPARATION_FAILED (contents withheld)',file=sys.stderr)
        return 2
    gold=[]
    for g in gold_in:
        gold.append({'qid':g['qid'],'type':g.get('type','unknown'),
                     'relevant_doc_ids':g.get('relevant_doc_ids',[]),
                     'confusable_doc_ids':g.get('confusable_doc_ids',[]),
                     'neutral_doc_ids':g.get('neutral_doc_ids',[])})
    if len({d['doc_id'] for d in docs})!=len(docs):p.error('duplicate_documents')
    if not docs or not any(q['text'] is not None for q in queries):p.error('nothing_to_evaluate')
    obj={'format':'aegisrag-local-embedding-eval-v1','pipeline':'step1-v5-nonlinked-v2-gate-v4-address-v2.2-region-v1',
         'hmac_key_tag':key_tag(key),'scope':'local_synthetic_holdout_only',
         'warning':'Pseudonymized does not guarantee zero residual PII; never send to remote endpoints.',
         'documents':docs,'queries':queries,'gold_for_evaluation_only':gold,
         'counts':{'document_states':dict(dc),'query_states':dict(qc),'docs_index_candidates':len(docs),
                   'queries_search_candidates':sum(q['text'] is not None for q in queries)}}
    private_write(a.out,obj)
    print('V5_PRIVATE_PREPARATION',json.dumps(obj['counts'],ensure_ascii=False))
    print('PRIVATE_OUTPUT',a.out)
    print('KEY_TAG',obj['hmac_key_tag'],'(not the key; do not commit private output)')
    return 0

if __name__=='__main__':raise SystemExit(main())
