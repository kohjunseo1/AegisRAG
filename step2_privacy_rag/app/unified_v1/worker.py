"""Trusted local model worker. Invoke with absolute private model-manifest path.

stdio is the sole IPC channel. No key/env loading, text/error logging or sockets.
"""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import socket
import sys

if __package__ in {None,''}:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unified_v1.adapters import LocalHFPredictor
from unified_v1.contracts import config_hash, fingerprint

MAX_BYTES=1048576

def main():
    try:
        if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():return 2
        manifest=json.loads(Path(sys.argv[1]).read_text())
        if manifest['source_id'] not in {'klue','koelectra'}:return 2
        request=json.loads(sys.stdin.buffer.read(MAX_BYTES+1))
        if (len(request['text'].encode())>MAX_BYTES or request['fingerprint']!=fingerprint(request['text'])
                or request['config_hash']!=config_hash(manifest)):
            return 2
        def blocked(*args,**kwargs):raise RuntimeError('WORKER_NETWORK_FORBIDDEN')
        # Retain socket class for libraries that subclass it; deny all connections.
        socket.socket.connect=blocked
        socket.socket.connect_ex=blocked
        socket.create_connection=blocked
        os.environ['HF_HUB_OFFLINE']='1'
        os.environ['TRANSFORMERS_OFFLINE']='1'
        with open(os.devnull,'w') as null, contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
            predictor=LocalHFPredictor(manifest['model_dir'],manifest['trusted_root'],manifest['hashes'],
                                       ignore_labels=manifest.get('ignore_labels',()),
                                       max_length=manifest.get('max_length',512),stride=manifest.get('stride',64))
            rows=predictor(request['text'])
        response=dict(request_id=request['request_id'],fingerprint=request['fingerprint'],
                      config_hash=request['config_hash'],candidates=rows)
        output=json.dumps(response).encode()
        if len(output)>MAX_BYTES:return 2
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        return 2

if __name__=='__main__':
    raise SystemExit(main())
