#!/usr/bin/env python3
"""Validate private Blind 100 QA Gold and convert source IDs to HMAC DB IDs.

The source, queries, Gold and converted eval JSONL remain LOCAL ONLY.
No model/DB access; raw question text and PII never appear in console logs.
"""
from __future__ import annotations
import argparse,json,os,re,sys,zipfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))

def rows(path):
    if not path.is_file() or path.stat().st_size>25000000:
        raise ValueError('BLIND_QA_INPUT_INVALID')
    try:
        data=[json.loads(x) for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    except (ValueError,UnicodeError):
        raise ValueError('BLIND_QA_JSONL_INVALID') from None
    if not data or any(not isinstance(x,dict) for x in data):
        raise ValueError('BLIND_QA_SCHEMA_INVALID')
    return data

def docs_from_zip(path):
    if not path.is_file():raise ValueError('BLIND_QA_ZIP_MISSING')
    with zipfile.ZipFile(path) as z:
        files=[x for x in z.infolist() if x.filename.endswith('.jsonl')
               and not x.filename.startswith('__MACOSX/') and not x.is_dir()]
        if len(files)!=1 or files[0].file_size>25000000:
            raise ValueError('BLIND_QA_ZIP_INVALID')
        try:data=[json.loads(x) for x in z.read(files[0]).decode('utf-8-sig').splitlines() if x.strip()]
        except (ValueError,UnicodeError):raise ValueError('BLIND_QA_CORPUS_INVALID') from None
    docs={}
    for d in data:
        if not isinstance(d,dict) or not isinstance(d.get('id'),str) or not isinstance(d.get('text'),str) or d['id'] in docs:
            raise ValueError('BLIND_QA_CORPUS_SCHEMA_INVALID')
        docs[d['id']]=d
    return docs

def validate(questions,gold,docs):
    by={g.get('qid'):g for g in gold}
    if len(questions)!=len(gold) or len(by)!=len(gold) or set(by)!={q.get('qid') for q in questions}:
        raise ValueError('BLIND_QA_QID_MISMATCH')
    seen=set()
    for q in questions:
        g=by[q['qid']]
        refs=g.get('relevant_source_ids')
        phrases=g.get('support_phrases')
        decision=g.get('expected_decision')
        query=q.get('query')
        if (not isinstance(query,str) or not 2<=len(query)<=1000 or query in seen
            or q.get('split') not in ('dev','validation','challenge')
            or q.get('split')!=g.get('split') or q.get('type')!=g.get('type')
            or decision not in ('ANSWER','CLARIFY','INSUFFICIENT_EVIDENCE')
            or not isinstance(refs,list) or len(refs)!=len(set(refs))
            or any(not isinstance(k,str) or k not in docs for k in refs)
            or (decision=='ANSWER')!=bool(refs)
            or not isinstance(phrases,list)
            or any(not isinstance(p,str) or not any(p in docs[k]['text'] for k in refs) for p in phrases)):
            raise ValueError('BLIND_QA_GOLD_NOT_GROUNDED')
        seen.add(query)
    return by

def prepare(questions,by,namespace,key,split):
    from dataset_workflow import doc_id
    return [dict(case_id=q['qid'],question=q['query'],category=q['type'],split=q['split'],
                 expected_decision=by[q['qid']]['expected_decision'],
                 expected_answer_for_private_review=by[q['qid']].get('expected_answer'),
                 relevant_doc_ids=sorted(set(doc_id(namespace,k,key) for k in by[q['qid']]['relevant_source_ids'])))
            for q in questions if split=='all' or q['split']==split]

def write_local(path,data):
    root=(ROOT/'step2_privacy_rag/local_eval').resolve()
    dst=path.expanduser().resolve()
    if not dst.is_relative_to(root) or dst.suffix!='.jsonl' or dst.exists():
        raise ValueError('BLIND_QA_OUTPUT_MUST_BE_NEW_LOCAL_JSONL')
    dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(dst.parent,0o700)
    fd=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        for row in data:out.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--documents',type=Path,required=True)
    p.add_argument('--queries',type=Path,required=True)
    p.add_argument('--gold',type=Path,required=True)
    p.add_argument('--namespace',default='blind_v1')
    p.add_argument('--split',choices=('all','dev','validation','challenge'),default='dev')
    p.add_argument('--check-only',action='store_true')
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    p.add_argument('--out',type=Path)
    a=p.parse_args(argv)
    try:
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,22}',a.namespace):
            raise ValueError('BLIND_QA_NAMESPACE_INVALID')
        questions=rows(a.queries)
        gold=rows(a.gold)
        original=docs_from_zip(a.documents)
        by=validate(questions,gold,original)
        info={'documents':len(original),'questions':len(questions),
              'splits':dict(Counter(q['split'] for q in questions)),
              'expected_decisions':dict(Counter(g['expected_decision'] for g in gold))}
        print('BLIND_QA_GOLD_CHECK',json.dumps(info,ensure_ascii=False),flush=True)
        if a.check_only:return 0
        if not a.out:raise ValueError('BLIND_QA_OUTPUT_REQUIRED')
        from dataset_preparation import fixed_key
        write_local(a.out,prepare(questions,by,a.namespace,fixed_key(a.env),a.split))
        print('BLIND_QA_CASES_READY',json.dumps({'split':a.split,'output':str(a.out)},ensure_ascii=False))
        return 0
    except Exception as exc:
        error=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'BLIND_QA_[A-Z0-9_]+',str(exc)) else type(exc).__name__
        print('BLIND_QA_FAILED',error,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
