#!/usr/bin/env python3
"""One-command LOCAL demonstration: chat + Q-001 review/experiment admin.

The chat is read-only and HCX-enabled ONLY with explicit external transfer
authorization. The admin may create a NEW, isolated 187-doc experimental table
after human review and operator confirmation; then the launcher restarts chat
pointed at that table. Existing 177-doc table is never altered.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_STATE=ROOT/"step2_privacy_rag/local_eval/q001_e2e_state.json"
IDS=["H0002","H0057","H0072","H0091","H0103",
     "H0105","H0116","H0117","H0119","H0135"]
TABLE=re.compile(r"rag\.aegis_repro_[a-f0-9]{20}\Z")


def clean_up(processes):
    for p in processes:
        if p is not None and p.poll() is None:
            p.send_signal(signal.SIGINT)
    for p in processes:
        if p is not None and p.poll() is None:
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.terminate()


def table_from_state(path:Path) -> str|None:
    try:
        d=json.loads(path.read_text(encoding="utf-8"))
        if (d.get("format")!="q001-e2e-index-v1" or d.get("documents")!=187
            or d.get("hcx_called") is not False
            or not TABLE.fullmatch(d.get("table",""))):
            return None
        return d["table"]
    except (ValueError,OSError,TypeError):
        return None


def port_in_use(port:int)->bool:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex(("127.0.0.1",port))==0


def admin_ready(port:int)->bool:
    try:
        with urlopen("http://127.0.0.1:"+str(port)+"/",timeout=1.5) as response:
            return response.status==200
    except (OSError,TimeoutError):
        return False


def launched(command,env):
    return subprocess.Popen([sys.executable,*command],
                            cwd=ROOT,env=env,stdin=subprocess.DEVNULL)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chat-port",type=int,default=8768)
    p.add_argument("--admin-port",type=int,default=8771)
    p.add_argument("--device",choices=("cpu","mps"),default="cpu")
    p.add_argument("--no-browser",action="store_true")
    a=p.parse_args()
    if not (1024<=a.chat_port<=65535 and 1024<=a.admin_port<=65535
            and a.chat_port!=a.admin_port):
        p.error("INVALID_DEMO_PORTS")
    if os.getenv("AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED")!="YES":
        p.error("Set AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED=YES after authorizing synthetic-data HCX")
    if port_in_use(a.admin_port):
        print("Q001_DEMO_FAILED ADMIN_PORT_ALREADY_IN_USE_"+str(a.admin_port),file=sys.stderr)
        return 2
    if port_in_use(a.chat_port):
        print("Q001_DEMO_FAILED CHAT_PORT_ALREADY_IN_USE_"+str(a.chat_port),file=sys.stderr)
        return 2
    env=os.environ.copy()
    since=DEFAULT_STATE.stat().st_mtime_ns if DEFAULT_STATE.exists() else 0
    admin=None
    chat=None
    try:
        admin=launched(["scripts/review_dashboard_q001.py","--enable-experimental-indexing",
                        "--port",str(a.admin_port),"--device",a.device,"--ids",*IDS],env)
        # Admin detector initialization can take time. Verify that the admin
        # actually starts before spending memory loading chat embedding models.
        while not admin_ready(a.admin_port):
            if admin.poll() is not None:
                raise RuntimeError("ADMIN_STARTUP_FAILED_CHECK_TERMINAL_OUTPUT")
            time.sleep(1)
        print("Q001_DEMO_ADMIN_CONFIRMED http://127.0.0.1:"+str(a.admin_port),flush=True)
        chat_env=env.copy()
        chat=launched(["scripts/aegisrag.py","run-chat","--port",str(a.chat_port),
                       "--device",a.device],chat_env)
        print("Q001_DEMO_ADMIN http://127.0.0.1:"+str(a.admin_port),flush=True)
        print("Q001_DEMO_CHAT http://127.0.0.1:"+str(a.chat_port),flush=True)
        print("ADMIN_SESSION_TOKEN_PRINTED_BY_ADMIN_PROCESS_ONLY",flush=True)
        opened=set()
        current=None
        while True:
            if admin.poll() is not None:
                raise RuntimeError("ADMIN_SERVICE_STOPPED")
            if chat.poll() is not None:
                raise RuntimeError("CHAT_SERVICE_STOPPED")
            if not a.no_browser:
                for name,port in (("admin",a.admin_port),("chat",a.chat_port)):
                    if name not in opened:
                        try:
                            with urlopen("http://127.0.0.1:"+str(port)+
                                         ("/api/health" if name=="chat" else "/"),
                                         timeout=1.5) as response:
                                ready=response.status==200
                        except (OSError,TimeoutError):
                            ready=False
                        if ready:
                            webbrowser.open("http://127.0.0.1:"+str(port))
                            opened.add(name)
            if DEFAULT_STATE.exists() and DEFAULT_STATE.stat().st_mtime_ns>since:
                target=table_from_state(DEFAULT_STATE)
                if target and target!=current:
                    # New experiment metadata is generated ONLY after isolated
                    # ingest+evaluation succeeds, then chat is relaunched.
                    print("Q001_DEMO_SWITCH_CHAT_TO_REVALIDATED_187",flush=True)
                    clean_up([chat])
                    chat_env=env.copy()
                    chat_env["AEGISRAG_TABLE"]=target
                    chat_env["AEGISRAG_EXPECTED_ROWS"]="187"
                    chat=launched(["scripts/aegisrag.py","run-chat","--port",str(a.chat_port),
                                   "--device",a.device],chat_env)
                    since=DEFAULT_STATE.stat().st_mtime_ns
                    current=target
                    opened.discard("chat")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print("Q001_DEMO_FAILED "+type(exc).__name__,file=sys.stderr)
        return 2
    finally:
        clean_up([chat,admin])
    return 0


if __name__=="__main__":
    raise SystemExit(main())
