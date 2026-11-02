"""Offline regression tests for detector boundaries and prediction handling."""
import unittest
from pii_detector.detector import (
    normalize_spans, validate_boundaries, PIIDetector, fuse_labelwise, chunk_windows
)


def span(text, sub, label='address'):
    p = text.index(sub)
    return {'label': label, 'start': p, 'end': p + len(sub)}


class TestDetectorBoundaries(unittest.TestCase):
    def test_parenthesized_postal_prefix(self):
        t='배송지: (05299) 서울특별시 강남구 한빛로 9'
        raw=[span(t,'서울특별시 강남구 한빛로 9')]
        p,ev=validate_boundaries(t,raw)
        self.assertEqual(t[p[0]['start']:p[0]['end']], '(05299) 서울특별시 강남구 한빛로 9')
        self.assertTrue(any(e['status']=='applied_parenthesized' for e in ev))
        p2,_=validate_boundaries(t,p)
        self.assertEqual(p,p2)

    def test_bare_labeled_postal_prefix(self):
        t='우편번호: 05299 서울특별시 강남구 한빛로 9'
        out,ev=validate_boundaries(t,[span(t,'서울특별시 강남구 한빛로 9')])
        self.assertEqual(t[out[0]['start']:out[0]['end']],'05299 서울특별시 강남구 한빛로 9')
        self.assertTrue(any(e['status']=='applied_bare' for e in ev))

    def test_bare_address_field_prefix(self):
        t='주소: 05299 경기도 성남시 분당구'
        out,_=validate_boundaries(t,[span(t,'경기도 성남시 분당구')])
        self.assertEqual(t[out[0]['start']:out[0]['end']],'05299 경기도 성남시 분당구')

    def test_unlabeled_bare_review_only(self):
        t='05299 서울특별시 강남구'
        out,events=validate_boundaries(t,[span(t,'서울특별시 강남구')])
        self.assertEqual(t[out[0]['start']:out[0]['end']], '서울특별시 강남구')
        self.assertTrue(any(e['status']=='review_unlabeled_prefix' for e in events))

    def test_bare_suffix_review_only(self):
        t='서울특별시 강남구 한빛로 9 05299'
        out,events=validate_boundaries(t,[span(t,'서울특별시 강남구 한빛로 9')])
        self.assertEqual(t[out[0]['start']:out[0]['end']], '서울특별시 강남구 한빛로 9')
        self.assertTrue(any(e['status']=='review_trailing_postcode' for e in events))

    def test_negative_product_code(self):
        for t in ['상품번호: 05299 서울특별시 강남구', '상품번호: (05299) 서울특별시 강남구']:
            out,_=validate_boundaries(t,[span(t,'서울특별시 강남구')])
            self.assertEqual(t[out[0]['start']:out[0]['end']], '서울특별시 강남구')

    def test_five_digit_customer_number_is_not_a_postal_code(self):
        for t, address in [('고객번호: 12345 서울특별시 강남구', '서울특별시 강남구'),
                            ('접수번호: 12345 경기도 성남시', '경기도 성남시')]:
            out,events=validate_boundaries(t,[span(t,address)])
            self.assertEqual(t[out[0]['start']:out[0]['end']], address)
            self.assertTrue(any(e['status']=='rejected_nonpostal_field' for e in events))

    def test_overlapping_postcode_candidate_not_added(self):
        t='배송지: (05299) 서울특별시 강남구'
        out,events=validate_boundaries(t,[span(t,'서울특별시 강남구'), span(t,'(05299)','phone_number')])
        self.assertEqual(out[-1]['start'],t.index('서울특별시'))
        self.assertTrue(any(e['status']=='review_overlap' for e in events))

    def test_no_name_trim(self):
        for name in ['오가은이','한가은이','이슬이']:
            t='직원 ' + name + ' 조건 안내를 맡았다.'
            item=span(t,name,'person')
            out,_=validate_boundaries(t,[item]);self.assertEqual(out,[item])

    def test_routing(self):
        t='김민수 서울특별시 강남구'
        ax=[span(t,'김민수','person'),span(t,'서울특별시 강남구')]
        veil=[span(t,'서울특별시 강남구'),span(t,'김민수','person')]
        result,conflict=fuse_labelwise(ax,veil)
        self.assertEqual({x['label'] for x in result},{'address','person'})
        self.assertEqual(len(conflict),0)

    def test_long_document_windowing(self):
        text=('일반 문장. '*110) + ' 김민수 ' + ('일반 문장. '*110)
        def ax(piece):
            import re
            return [{'label':'person','start':x.start(),'end':x.end()} for x in re.finditer('김민수',piece)]
        def veil(piece):return []
        with PIIDetector(ax,veil) as det:
            result=det.detect(text)
        self.assertEqual(result['status'],'READY_FOR_PSEUDONYMIZATION')
        self.assertEqual(result['entities'],[span(text,'김민수','person')])
        self.assertGreater(result['windows'],1)

    def test_backend_error_failclosed(self):
        def failed(piece): raise RuntimeError('has sensitive data')
        with PIIDetector(failed, lambda x:[]) as det:
            result=det.detect('김민수')
        self.assertEqual(result['status'],'BLOCKED')
        self.assertNotIn('sensitive',str(result))

    def test_exceed_max_length_failclosed(self):
        with PIIDetector(lambda t: [], lambda t: [], max_chars=5) as det:
            r=det.detect('0123456')
        self.assertEqual(r['status'],'BLOCKED')

    def test_no_raw_strings_in_output(self):
        t='고객명: 오가은이 조건 안내. 주소: 05299 서울특별시 강남구'
        def ax(piece):
            import re
            return [{'label':'person','start':m.start(),'end':m.end()} for m in re.finditer('오가은이',piece)]
        def veil(piece):
            import re
            return [{'label':'address','start':m.start(),'end':m.end()} for m in re.finditer('서울특별시 강남구',piece)]
        with PIIDetector(ax,veil) as det:
            r=det.detect(t)
        self.assertEqual(r['status'],'REVIEW_REQUIRED')
        self.assertNotIn('오가은',str(r))
        self.assertEqual({x['label'] for x in r['entities']},{'person','address'})

if __name__=='__main__': unittest.main(verbosity=2)
