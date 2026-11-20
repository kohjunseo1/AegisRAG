"""Conservative evidence-qualified local PII mask candidates for V7.1.

Standalone, non-deployed. Structural candidates are MASK/REVIEW evidence,
NOT canonical typed PERSON identities or HMAC aliases. No Gold dependence.
Read-only; caller must fail closed on unresolved collisions and measure FP.
"""
from __future__ import annotations
import re

_PATTERNS = (
    ("email", re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z])")),
    ("phone_number", re.compile(r"(?<!\d)(?:01[016789]|02|0[3-6][1-5])[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")),
    ("resident_registration_number", re.compile(r"(?<!\d)\d{6}[- ]?\d{7}(?!\d)")),
    ("card_number", re.compile(r"(?<!\d)(?:\d{4}[-. ]?){3}\d{4}(?!\d)")),
    ("account_number", re.compile(r"(?<!\d)\d{2,6}[- ]?\d{2,6}[- ]?\d{2,6}(?!\d)")),
)
_CUES = {
    'email':re.compile(r'이메일|전자우편|메일|email', re.I),
    'phone_number':re.compile(r'전화|연락|휴대폰|휴대전화|phone|mobile',re.I),
    'resident_registration_number':re.compile(r'주민등록|주민번호|주민 등록',re.I),
    'card_number':re.compile(r'카드|신용|결제수단|card',re.I),
    'account_number':re.compile(r'계좌|입금|출금|환급|예금주|account',re.I),
}
_NEGATIVE_CUES=re.compile(r'문의번호|접수번호|송장번호|주문번호|관리번호|거래번호|업무참조|문서번호')


def _same_line_prefix(text, start, width=48):
    line_start=text.rfind('\n',0,start)+1
    return text[max(line_start,start-width):start]


def _context_ok(text, label, start):
    # A local cue must be the closest *field* before the numeric span;
    # a distant '카드' cannot relabel a neighboring bank account number.
    prefix=_same_line_prefix(text,start)
    matches=[]
    for key,regex in _CUES.items():
        for m in regex.finditer(prefix):
            matches.append((m.end(),key))
    for m in _NEGATIVE_CUES.finditer(prefix):
        matches.append((m.end(),'NOT_PII'))
    if not matches:
        return label=='email'
    # Cues more than 30 characters from the value are not authoritative.
    endpoint,nearest=max(matches)
    return nearest==label and len(prefix)-endpoint<=30


def _overlap(a,b):
    return a['start'] < b['end'] and b['start'] < a['end']


def candidates(text):
    """Return typed *candidates*, with offsets only. Never emit source text."""
    found=[]
    if not isinstance(text,str):raise TypeError('text must be str')
    for label,pattern in _PATTERNS:
        for match in pattern.finditer(text):
            s,e=match.span()
            if not _context_ok(text,label,s):continue
            c={'label':label,'start':s,'end':e,'source':'STRUCTURAL_REVIEW_CANDIDATE'}
            # PII types are mutually exclusive for a single numeric run.
            if any(_overlap(c,other) for other in found):continue
            found.append(c)
    return sorted(found,key=lambda c:(c['start'],c['end']))


def mask_supplements(text, existing_masks):
    """Only propose intervals not already covered; never create HMAC identity.

    Every returned span requires review/independent verification before an
    application treats the span as an approved typed entity.
    """
    existing=[]
    for m in existing_masks:
        a,b=int(m['start']),int(m['end'])
        if not 0<=a<b<=len(text):raise ValueError('INVALID_EXISTING_MASK')
        existing.append((a,b))
    out=[]
    for c in candidates(text):
        a,b=c['start'],c['end']
        if any(a<xend and xstart<b for xstart,xend in existing):
            # An overlapped partial token may need span extension; return
            # candidate for explicit reconciliation, not silent rewrites.
            uncovered=any(not any(xstart<=pos<xend for xstart,xend in existing)
                          for pos in range(a,b))
            if not uncovered:continue
        out.append(dict(c))
    return out
