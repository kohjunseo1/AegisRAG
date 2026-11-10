"""Local-only, conservative region lookup keys (not PII declassification).

Never export raw region strings or keys to external services. Gold annotations
must not be used as a retrieval/index feature. Derive document keys only from
Gate-approved ADDRESS spans and query keys from locally approved query text.
"""
from __future__ import annotations
import hashlib
import hmac
import re
import unicodedata
from local_pseudonymizer_v2 import REGION_ALIASES, canonicalize_address_v22

_PROVINCES = '|'.join(re.escape(x) for x in sorted(REGION_ALIASES, key=len, reverse=True))
_CITY = r'[가-힣0-9]{1,12}(?:시|군|구)'
_SUFFIX = r'(?=$|\s|[,.(]|에|의|에서|관련|거주)'
_REGION_RX = re.compile(
    rf'(?<![가-힣])(?P<province>{_PROVINCES})\s+(?P<city>{_CITY}){_SUFFIX}'
)
_SUB_RX = re.compile(
    rf'\s+(?P<sub>[가-힣0-9]{{1,12}}(?:구|군)){_SUFFIX}'
)


def _paths(text: str, *, leading_only: bool) -> set[str]:
    s = unicodedata.normalize('NFKC', text).strip()
    found = set()
    matches = [_REGION_RX.match(s)] if leading_only else _REGION_RX.finditer(s)
    for m in matches:
        if m is None:
            continue
        root = REGION_ALIASES[m.group('province')]
        city = m.group('city')
        base = root + '/' + city
        found.add(base)
        sub = _SUB_RX.match(s, m.end())
        if sub and city.endswith('시'):
            found.add(base + '/' + sub.group('sub'))
    return found


def _key(secret: bytes, path: str) -> str:
    if type(secret) is not bytes or len(secret) < 32:
        raise ValueError('invalid_region_hmac_key')
    msg = b'AEGISRAG:REGION:V1\x00' + path.encode('utf-8')
    return 'region_v1_' + hmac.new(secret, msg, hashlib.sha256).hexdigest()[:24]


def keys_from_gate(raw: str, gate: dict, secret: bytes) -> tuple[str, ...]:
    out = set()
    masks = gate.get('mask_intervals', [])
    for ent in gate.get('entities_for_pseudonymization', []):
        if not isinstance(ent, dict) or ent.get('label') != 'address':
            continue
        a, b = ent.get('start'), ent.get('end')
        if not (type(a) is int and type(b) is int and 0 <= a < b <= len(raw)):
            raise ValueError('invalid_address_span')
        if not any(type(m.get('start')) is int and type(m.get('end')) is int
                   and m['start'] <= a and b <= m['end'] for m in masks):
            raise ValueError('address_not_protected')
        # Only extract from a Gate-approved address span, never from Gold.
        for path in _paths(canonicalize_address_v22(raw[a:b]), leading_only=True):
            out.add(_key(secret, path))
    return tuple(sorted(out))


def keys_from_query(raw: str, secret: bytes) -> tuple[str, ...]:
    # Called only after READY, locally, with no external transfer.
    return tuple(sorted(_key(secret, p) for p in _paths(raw, leading_only=False)))
