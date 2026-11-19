"""Small experimental typed resolver. No model, Gold, dataset or transport access."""
from __future__ import annotations
import re
from .contracts import ContractError, LABELS
from .protection import merge_intervals, StructuralSentinel

VERSION='lean-v1-20261119'
TITLE=r'(?:님|씨|대리|과장|차장|부장|팀장|주임|사원|책임|선임|드림|올림)'
FIELD=re.compile(r'(?:성명|이름|고객명|담당자|신청인|예금주)\s*[:：]\s*$')

def checked(text,spans):
    out={}
    for s in spans:
        l,a,b=s['label'],s['start'],s['end']
        if l not in LABELS or type(a)!=int or type(b)!=int or not 0<=a<b<=len(text):
            raise ContractError('LEAN_INVALID_SPAN')
        out[l,a,b]=dict(label=l,start=a,end=b)
    return sorted(out.values(),key=lambda s:(s['start'],s['end'],s['label']))

def person_boundaries(text,spans):
    """Explicit titles/signatures; ambiguous final 이 needs within-doc evidence."""
    out=[]
    for s in spans:
        s=dict(s);a,b=s['start'],s['end']
        if s['label']=='person':
            w=text[a:b]
            m=re.search(r'(?:\s*(?:님|씨|드림|올림)|께서|에게서|에게|이랑)$',w)
            if m and len(re.sub(r'\s','',w[:m.start()]))>=2:s['end']=b=a+m.start()
            # A real name ending 이 followed by a title always remains intact.
            if text[a:b].endswith('이') and b-a>=3 and not re.match(r'\s*'+TITLE+r'(?![가-힣])',text[b:]):
                short=text[a:b-1]
                short_ev=re.search(r'(?<![가-힣])'+re.escape(short)+r'\s*'+TITLE+r'(?![가-힣])',text)
                long_ev=re.search(r'(?<![가-힣])'+re.escape(text[a:b])+r'\s*'+TITLE+r'(?![가-힣])',text)
                if short_ev and not long_ev:s['end']=b-1
        out.append(s)
    return checked(text,out)

def corrections(text,raw,boost,pipeline,enabled=('person','address','numbers')):
    spans=checked(text,raw)
    if 'person' in enabled:spans=person_boundaries(text,spans)
    if 'address' in enabled:spans=boost.refine_addresses(text,spans)
    if 'numbers' in enabled:
        # Reuse original shape + field-context validators; never a 13-digit shortcut.
        spans=pipeline._post_union(text,spans,pipeline.rule_detect(text))
        spans=pipeline._fix_country_code(text,spans)
    return checked(text,spans)

def typed_plan(text,spans,raw, *, review=()):
    """Separate monotone protection from typed HMAC confirmation.

    No raw confidence comparisons or name lexicons. Names need explicit field,
    title, or another occurrence with that evidence. Uncertain spans are masked
    in a local preview but require review before LOCAL_PREPARED.
    """
    from local_pseudonymizer import canonicalize
    spans=checked(text,spans);raw=checked(text,raw)
    sentinel=[dict(label=c.label,start=c.start,end=c.end) for c in StructuralSentinel().candidates(text) if c.label in LABELS]
    masks=merge_intervals([(s['start'],s['end']) for s in raw+spans+sentinel+list(review)],len(text))
    strong=set()
    for s in spans:
        if s['label']=='person' and (FIELD.search(text[max(0,s['start']-24):s['start']]) or re.match(r'\s*'+TITLE+r'(?![가-힣])',text[s['end']:])):
            strong.add(re.sub(r'\s','',text[s['start']:s['end']]))
    hmac=[];pending=[]
    for s in spans:
        l,a,b=s['label'],s['start'],s['end'];value=text[a:b];ok=False
        left=text[max(0,a-35):a]
        conflict=any(t!=s and a<t['end'] and b>t['start'] for t in spans)
        if l=='person':ok=re.sub(r'\s','',value) in strong
        elif l=='address':ok=bool(re.search(r'(?:로|길|동|읍|면|리)\s*\d',value))
        elif l=='email':ok=bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value))
        elif l=='phone_number':ok=bool(re.fullmatch(r'[+\d(). -]+',value)) and len(re.sub(r'\D','',value))>=9
        elif l=='resident_registration_number':ok=bool(re.search(r'주민|등록번호|RRN',left,re.I)) and bool(re.fullmatch(r'\d{6}\s*[-.]\s*[1-8]\d{6}',value))
        elif l=='account_number':ok=bool(re.search(r'계좌|입금|송금|은행|예금|통장|환불',left))
        elif l=='card_number':ok=bool(re.search(r'카드|card',left,re.I))
        try:canonicalize(l,value)
        except ValueError:ok=False
        (hmac if ok and not conflict else pending).append(s)
    # A structural finding not wholly represented by a typed proposal also waits.
    for s in sentinel+list(review):
        if not any(t['start']<=s['start'] and s['end']<=t['end'] for t in spans):pending.append(s)
    # Residual mask-only coverage never implicitly receives auto approval.
    confirmed=merge_intervals([(s['start'],s['end']) for s in hmac],len(text))
    from .protection import covers
    for a,b in masks:
        if not covers((a,b),confirmed):pending.append(dict(label='mask_only',start=a,end=b))
    return dict(pred=spans,hmac=hmac,masks=masks,pending=pending,state='REVIEW_REQUIRED' if pending else 'LOCAL_PREPARED')

def materialize(text,plan,key, *, preview=False):
    from local_pseudonymizer import pseudonymize_local
    if plan['state']!='LOCAL_PREPARED' and not preview:raise ContractError('LEAN_PLAN_REVIEW_REQUIRED')
    return pseudonymize_local(text,dict(decision='READY_FOR_LOCAL_PSEUDONYMIZATION',entities=plan['pred'],
        entities_for_pseudonymization=plan['hmac'],mask_intervals=[dict(start=a,end=b) for a,b in plan['masks']],redaction_intervals=[]),hmac_key=key)
