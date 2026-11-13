#!/usr/bin/env python3
"""Locally stage new JSONL, review corrected source, export approved batch."""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'step2_privacy_rag/app'))
sys.path.insert(0,str(ROOT/'step1_pii_detector/src'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['stage','status','review','export'])
    p.add_argument('--input',type=Path)
    p.add_argument('--stage',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--dataset')
    p.add_argument('--id-field',default='id')
    p.add_argument('--text-field',default='text')
    p.add_argument('--title-field')
    p.add_argument('--type-field')
    p.add_argument('--doc-id')
    p.add_argument('--revised-source-file',type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--env',type=Path,default=ROOT/'.env')
    a=p.parse_args(argv)
    from dataset_preparation import fixed_key
    from dataset_workflow import (stage_dataset, input_rows, write_private,
        signed, read_signed, review_corrected, export_approved, summary)
    try:
        key=fixed_key(a.env)
        if a.command=='stage':
            if not a.input or not a.dataset or not a.stage:
                p.error('stage: --input --dataset --stage required')
            records=input_rows(a.input,id_field=a.id_field,text_field=a.text_field,
                               title_field=a.title_field,type_field=a.type_field)
            from dotenv import load_dotenv
            load_dotenv(a.env)
            from pii_detector import create_detector
            model_started=time.perf_counter()
            detector=create_detector()
            model_load_ms=(time.perf_counter()-model_started)*1000
            pipeline_started=time.perf_counter()
            with detector:
                stage=stage_dataset(records,a.dataset,detector,key)
            batch_ms=(time.perf_counter()-pipeline_started)*1000
            write_private(a.stage,signed(stage,key))
            print('INTAKE_STAGE',json.dumps(summary(stage),ensure_ascii=False))
            print('INTAKE_STAGE_TIMING_MS',json.dumps({
                'cold_detector_load':round(model_load_ms,2),
                'privacy_stage_documents':len(records),
                'privacy_stage_total':round(batch_ms,2),
                'mean_per_document':round(batch_ms/len(records),2),
                'includes':'Document detector, Gate, pseudonymization and in-memory staging; excludes KoE5 embedding and DB insertion.'
            },ensure_ascii=False))
        elif a.command=='status':
            if not a.stage:
                p.error('status: --stage required')
            print('INTAKE_STATUS',json.dumps(summary(read_signed(a.stage,key)),ensure_ascii=False))
        elif a.command=='review':
            if not (a.stage and a.doc_id and a.revised_source_file and a.reviewer):
                p.error('review: --stage --doc-id --revised-source-file --reviewer required')
            stage=read_signed(a.stage,key)
            if not a.revised_source_file.is_file():
                raise ValueError('REVISED_SOURCE_FILE_MISSING')
            revised=a.revised_source_file.read_text(encoding='utf-8')
            from dotenv import load_dotenv
            load_dotenv(a.env)
            from pii_detector import create_detector
            with create_detector() as detector:
                review_corrected(stage,a.doc_id,revised,a.reviewer,detector,key)
            write_private(a.stage,signed(stage,key),replace=True)
            print('INTAKE_REVIEW',json.dumps(summary(stage),ensure_ascii=False))
        else:
            if not a.stage or not a.output:
                p.error('export: --stage --output required')
            stage=read_signed(a.stage,key)
            data=export_approved(stage)
            write_private(a.output,data)
            print('INTAKE_EXPORT',json.dumps({'approved':len(data['documents']),
                'pending':len(stage['documents'])-len(data['documents'])},ensure_ascii=False))
    except Exception as exc:
        reason=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[A-Z][A-Z0-9_]+',str(exc)) else type(exc).__name__
        print('INTAKE_FAILED',a.command,reason,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
