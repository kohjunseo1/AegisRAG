#!/usr/bin/env python3
"""Inspect tracked/staged blobs and reachable history without printing secrets."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def forbidden(path):
    parts = Path(path).parts
    name = Path(path).name
    return ('local_eval' in parts or any(x.endswith('_private') for x in parts)
            or name == '.env' or (name.startswith('.env.') and name != '.env.example')
            or name.endswith(('.jsonl','.dump','.safetensors','.onnx','.pyc','.pem','.key','.sql.gz'))
            or '__pycache__' in parts or '.cache' in parts or '.vscode' in parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history',action='store_true', help='scan HEAD ancestry that an ordinary branch push sends')
    parser.add_argument('--all-refs',action='store_true', help='include local tool snapshots; never push these refs')
    args = parser.parse_args()
    secrets = []
    if (ROOT/'.env').is_file():
        from dotenv import dotenv_values
        for key,value in dotenv_values(ROOT/'.env').items():
            if value and len(value) >= 8 and re.search('PASSWORD|TOKEN|HMAC_KEY|API_KEY',key):
                secrets.append(value.encode())
    patterns = [re.compile(rb'hf_[A-Za-z0-9]{20,}'),re.compile(rb'sk-[A-Za-z0-9_-]{24,}'),
                re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
                re.compile(rb'postgres(?:ql)?://[^\s:]+:[^\s@]+@')]
    failures = set(); scanned=set()
    def inspect(oid,path):
        if forbidden(path):
            failures.add(('PRIVATE_PATH',path))
        if oid in scanned:return
        scanned.add(oid)
        kind=git('cat-file','-t',oid).strip()
        if kind != b'blob':return
        payload=git('cat-file','blob',oid)
        if any(s in payload for s in secrets) or any(p.search(payload) for p in patterns):
            failures.add(('SECRET_CONTENT',path))
    for row in git('ls-files','--stage','-z').split(b'\0'):
        if not row:continue
        meta,path=row.split(b'\t',1)
        inspect(meta.split()[1].decode(),path.decode())
    if args.history or args.all_refs:
        revision = '--all' if args.all_refs else 'HEAD'
        for row in git('rev-list','--objects',revision).splitlines():
            values=row.decode().split(' ',1)
            if len(values)==2:inspect(*values)
    for reason,path in sorted(failures):
        print(reason,path)
    print('GIT_SECURITY_SCAN', 'FAILED' if failures else 'PASS', 'objects='+str(len(scanned)),
          'history='+str(args.history), 'all_refs='+str(args.all_refs), 'local_secret_values_checked='+str(len(secrets)))
    return 1 if failures else 0


if __name__=='__main__':
    raise SystemExit(main())
