"""Offline pseudonymizer tests (no model, DB, secret, network)."""
from __future__ import annotations

from entity_pseudonymizer import (
    READY, PseudonymizationError, canonicalize, make_hmac_token, pseudonymize_local,
)

TEST_KEY = b"local-unit-test-only-key-DO-NOT-USE-IN-PROD-42"


def span(text, value, label=None, start=None):
    a = text.index(value) if start is None else start
    result = {"start": a, "end": a + len(value)}
    if label is not None:
        result["label"] = label
    return result


def gate(text, *, entities=(), roles=(), extra_masks=(), decision=READY):
    masks = [dict(start=x['start'],end=x['end']) for x in entities]
    masks += [dict(r) for r in roles] + [dict(x) for x in extra_masks]
    return {
        "decision": decision,
        "entities": list(entities),
        "entities_for_pseudonymization": list(entities),
        "redaction_intervals": list(roles),
        "mask_intervals": masks,
    }


def raises(code, callback):
    try:
        callback()
    except PseudonymizationError as exc:
        assert str(exc) == code, (str(exc),code)
    else:
        raise AssertionError('Expected fail-closed: ' + code)


def main():
    count=0
    a='고객 홍 길동님'
    b='고객 홍길동님'
    pa=span(a,'홍 길동','person');pb=span(b,'홍길동','person')
    oa=pseudonymize_local(a,gate(a,entities=[pa]),hmac_key=TEST_KEY,include_vault_records=True)
    ob=pseudonymize_local(b,gate(b,entities=[pb]),hmac_key=TEST_KEY)
    assert oa.safe_token_manifest[0]['token']==ob.safe_token_manifest[0]['token']
    assert '홍 길동' not in oa.sanitized_text and '홍길동' not in ob.sanitized_text
    assert oa.vault_records and oa.vault_records[0]['original']=='홍 길동'
    assert ob.vault_records is None
    count+=1

    text='고객명: 홍길동 / 연락처 010-1234-5678'
    p=span(text,'홍길동','person');n=span(text,'010-1234-5678','phone_number')
    r=pseudonymize_local(text,gate(text,entities=[p,n]),hmac_key=TEST_KEY)
    assert r.hmac_entity_count==2 and '<PERSON_' in r.sanitized_text and '<PHONE_NUMBER_' in r.sanitized_text
    assert '홍길동' not in r.sanitized_text and '010-1234-5678' not in r.sanitized_text
    count+=1

    text='김 과장님이 보고함.'
    role=span(text,'김 과장님')
    r=pseudonymize_local(text,gate(text,roles=[role]),hmac_key=TEST_KEY)
    assert r.sanitized_text=='[ROLE_REFERENCE]이 보고함.' and r.hmac_entity_count==0
    assert r.role_redaction_count==1
    count+=1

    text='표식 12345 확인'
    extra=span(text,'12345')
    r=pseudonymize_local(text,gate(text,extra_masks=[extra]),hmac_key=TEST_KEY)
    assert r.sanitized_text=='표식 [REDACTED_UNCLASSIFIED] 확인'
    assert r.residual_redaction_count==1 and r.residual_redacted_chars==5
    count+=1

    text='ABC 홍길동 DEF'
    p=span(text,'홍길동','person')
    extra={'start':p['start']-1,'end':p['end']+1}
    r=pseudonymize_local(text,gate(text,entities=[p],extra_masks=[extra]),hmac_key=TEST_KEY)
    assert r.residual_redaction_count==2 and r.replaced_source_chars==len(' 홍길동 ')
    assert '홍길동' not in r.sanitized_text
    count+=1

    # Safe passthrough for 0-PII, READY input (no post-inference transfer approval).
    text='일반 문서'
    r=pseudonymize_local(text,gate(text),hmac_key=TEST_KEY)
    assert r.sanitized_text==text and r.replaced_source_chars==0
    count+=1

    text='홍길동'
    p=span(text,text,'person')
    raises('gate_not_ready', lambda:pseudonymize_local(text,gate(text,entities=[p],decision='REVIEW_REQUIRED'),hmac_key=TEST_KEY))
    raises('gate_not_ready', lambda:pseudonymize_local(text,gate(text,entities=[p],decision='BLOCKED'),hmac_key=TEST_KEY))
    count+=2

    raises('missing_or_weak_hmac_key',lambda:pseudonymize_local(text,gate(text,entities=[p]),hmac_key=b'weak'))
    count+=1

    badly=gate(text,entities=[p]);badly['mask_intervals']=[]
    raises('unmasked_entity_or_role',lambda:pseudonymize_local(text,badly,hmac_key=TEST_KEY))
    count+=1

    conflict=gate('홍길동',entities=[p]);conflict['redaction_intervals']=[p]
    raises('conflicting_entity_or_role_spans',lambda:pseudonymize_local('홍길동',conflict,hmac_key=TEST_KEY))
    count+=1

    missing=gate(text,entities=[p]);missing.pop('mask_intervals')
    raises('invalid_mask_intervals',lambda:pseudonymize_local(text,missing,hmac_key=TEST_KEY))
    count+=1

    invalid=gate(text,entities=[p]);invalid['entities_for_pseudonymization']=[{**p,'label':'unknown_label'}]
    raises('invalid_entity_label',lambda:pseudonymize_local(text,invalid,hmac_key=TEST_KEY))
    count+=1

    assert make_hmac_token(TEST_KEY,'person','홍 길동')[0] != make_hmac_token(TEST_KEY,'address','홍 길동')[0]
    assert canonicalize('email','User@EXAMPLE.COM')=='User@example.com'
    assert canonicalize('phone_number','+82 10-1234-5678')==canonicalize('phone_number','010-1234-5678')
    count+=3

    # Explicitly verify V4 integration with a fully synthetic fake detector.
    from privacy_gate import check_privacy_gate
    class Fake:
        def __init__(self,output,ax=(),veil=()):self.output=output;self.ax=list(ax);self.veil=list(veil)
        def detect(self,text):return self.output
        def ax_predict(self,text):return self.ax
        def veil_predict(self,text):return self.veil
    text='고객 홍 길동님, 김 과장님'
    one=span(text,'홍','person');two=span(text,'길동','person')
    veil=span(text,'홍 길동','person');extra=span(text,'김','person')
    raw_gate={'status':'READY_FOR_PSEUDONYMIZATION',
              'entities':[one,two],
              'mask_intervals':[{'start':one['start'],'end':one['end']},{'start':two['start'],'end':two['end']}]}
    output=check_privacy_gate(Fake(raw_gate,ax=[one,two],veil=[veil,extra]),text)
    assert output['decision']==READY,output.get('reason')
    assert output['person_merges']==1 and len(output['redaction_intervals'])==1
    r=pseudonymize_local(text,output,hmac_key=TEST_KEY)
    assert '홍 길동' not in r.sanitized_text and '김 과장님' not in r.sanitized_text
    assert r.hmac_entity_count==1 and r.role_redaction_count==1
    count+=1

    # Contradictory strict spans (overlap) fail closed.
    malformed=gate('홍길동',entities=[p,p])
    raises('conflicting_entity_or_role_spans',lambda:pseudonymize_local('홍길동',malformed,hmac_key=TEST_KEY))
    count+=1

    print(f'로컬 가명처리 V1 합성 계약 테스트 {count}개 PASS | 모델/DB/외부 호출 없음')

if __name__ == '__main__':main()

import unittest

class LegacyContract(unittest.TestCase):
    def test_contract(self):
        main()
