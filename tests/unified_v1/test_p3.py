from dataclasses import replace
import unittest
from local_pseudonymizer import make_hmac_token, canonicalize, canonicalize_address_v22
from unified_v1.contracts import PersonMention, ContractError
from unified_v1.identity import pseudonym_token, OwnedAnchor, IdentityBundle, resolve, extract_owned_fields
from unified_v1.protection import fuse, prepare
from test_p2 import KEY, TAG, baseline, evidence

class IdentityTests(unittest.TestCase):
    def bundle(self,mid,name='name-key',phone='p1',email='e1'):
        attrs=[(lab,key) for lab,key in [('phone_number',phone),('email',email)] if key]
        m=PersonMention(mid,'d_'+mid,name,tuple(k for lab,k in attrs),('EXPLICIT_FIELD_OWNER',))
        return IdentityBundle((m,),tuple(OwnedAnchor(k,lab,mid,'d_'+mid) for lab,k in attrs),TAG)

    def test_hmac_frozen_parity_all_labels_and_query_document(self):
        values={'person':'금 재원','phone_number':'010-0000-0000','email':'SYNTH@example.invalid',
                'address':'서울시 강남구 합성로 1','resident_registration_number':'000101-3000000',
                'account_number':'123-456-789','card_number':'0000-0000-0000-0000'}
        golden={'person':'<PERSON_94639681e15da56c5559f2ed>',
                'phone_number':'<PHONE_NUMBER_c681bd9fcda2ecdf38f1d4c2>',
                'email':'<EMAIL_ff452d296d2396da0461b656>',
                'address':'<ADDRESS_c275dcda3261cec454d98451>',
                'resident_registration_number':'<RESIDENT_REGISTRATION_NUMBER_8c31be1ea37fef5da4f10a6c>',
                'account_number':'<ACCOUNT_NUMBER_b489625453344607b4c91cf0>',
                'card_number':'<CARD_NUMBER_9e91c10b684305a0b97d390b>'}
        for label,value in values.items():
            expected=golden[label]
            self.assertEqual(make_hmac_token(KEY,label,value)[0],expected)
            self.assertEqual(pseudonym_token(KEY,label,value),expected)
            self.assertRegex(expected,r'^<[A-Z_]+_[0-9a-f]{24}>$')
            text=value
            span=dict(label=label,start=0,end=len(text))
            for kind in ['document','query']:
                p=fuse(text,evidence(text,[span],kind),baseline([span]))
                result=prepare(text,p,input_kind=kind,hmac_key=KEY,key_tag=TAG)
                self.assertEqual(result.sanitized_text,expected)
        self.assertNotEqual(pseudonym_token(KEY,'account_number','123456'),pseudonym_token(KEY,'card_number','123456'))
        self.assertEqual(canonicalize('person','금 재원'),canonicalize('person','금재원'))
        addr=canonicalize_address_v22('서울시 강남구 합성로 1')
        self.assertEqual(canonicalize_address_v22(addr),addr)

    def test_only_two_explicit_independent_attributes_link(self):
        result=resolve([self.bundle('a'),self.bundle('b')],hmac_key=KEY,key_tag=TAG)
        self.assertEqual(result[0].entity_id,result[1].entity_id)
        self.assertEqual(result[0].link_status,'LINKED')
        result=resolve([self.bundle('a',email=None),self.bundle('b',email=None)],hmac_key=KEY,key_tag=TAG)
        self.assertTrue(all(m.link_status=='UNRESOLVED' for m in result))

    def test_homonyms_changed_phone_shared_phone_no_attributes(self):
        cases=[ [self.bundle('a'),self.bundle('b',phone='p2')],
                [self.bundle('a'),self.bundle('b',name='other')],
                [self.bundle('a',phone=None,email=None),self.bundle('b',phone=None,email=None)],
                [self.bundle('a'),self.bundle('b'),self.bundle('c',phone='p2')] ]
        for bundles in cases:
            self.assertTrue(all(m.link_status=='UNRESOLVED' for m in resolve(bundles,hmac_key=KEY,key_tag=TAG)))

    def test_roles_name_only_and_attribute_ownership(self):
        text='고객명: 금재원\n전화: 010-0000-0000\n이메일: synthetic@example.invalid\n\n담당자: 금재원'
        spans=[]
        for label,value in [('person','금재원'),('phone_number','010-0000-0000'),('email','synthetic@example.invalid')]:
            start=0
            while (start:=text.find(value,start))>=0:
                spans.append(dict(label=label,start=start,end=start+len(value)));start+=len(value)
        p=fuse(text,evidence(text,spans),baseline(spans))
        self.assertFalse(p.review_reason_codes)
        b=extract_owned_fields(text,p,doc_id='synthetic_doc',hmac_key=KEY,key_tag=TAG)
        self.assertEqual(len(b.mentions),2)
        self.assertEqual(len(b.mentions[0].attribute_key_ids),2)
        self.assertEqual(b.mentions[1].attribute_key_ids,())
        self.assertEqual(len(b.anchors),2)

    def test_key_mismatch_and_forged_ownership(self):
        with self.assertRaises(ContractError):resolve([self.bundle('a')],hmac_key=KEY,key_tag='wrong')
        b=self.bundle('a')
        b=replace(b,anchors=(replace(b.anchors[0],relation_type='CO_OCCURRENCE'),b.anchors[1]))
        with self.assertRaises(ContractError):resolve([b],hmac_key=KEY,key_tag=TAG)
