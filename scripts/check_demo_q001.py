#!/usr/bin/env python3
"""Local, read-only diagnostic for chat and admin demonstration servers.

No secrets, raw queries, human annotations or DB connectivity are inspected.
"""
from __future__ import annotations
import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

def probe(url):
    try:
        with urlopen(url,timeout=3) as response:
            payload=response.read(4096)
            return response.status, payload
    except HTTPError as e:
        return e.code, e.read(4096)
    except (OSError,URLError,TimeoutError):
        return None,b""

def main():
    chat,raw=probe("http://127.0.0.1:8768/api/health")
    try: c=json.loads(raw) if chat==200 else {}
    except (ValueError,UnicodeError):c={}
    admin,_=probe("http://127.0.0.1:8771/")
    guarded,raw=probe("http://127.0.0.1:8771/api/cases")
    try: auth=json.loads(raw).get("error") if guarded else None
    except (ValueError,UnicodeError):auth=None
    out={
        "chat_running":chat==200 and c.get("status")=="OK",
        "chat_http_status":chat,
        "chat_expected_index_rows":c.get("expected_index_rows"),
        "chat_target":c.get("index_kind"),
        "admin_running":admin==200 and guarded==403 and auth=="AUTH_REQUIRED",
        "admin_http_status":admin,
        "admin_auth_guard_http_status":guarded,
        "admin_auth_guard_message":auth,
        "note":"ADMIN_AUTH_REQUIRED_IS_EXPECTED_WITHOUT_SESSION_TOKEN",
        "no_db_writes":True,
    }
    print("Q001_DEMO_DIAG "+json.dumps(out,ensure_ascii=False,sort_keys=True))
    if not out["admin_running"]:
        print("Q001_ADVICE: review dashboard may be stopped, on a different port, or not current.")
    if not out["chat_running"]:
        print("Q001_ADVICE: chat server may be stopped or using a different port.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
