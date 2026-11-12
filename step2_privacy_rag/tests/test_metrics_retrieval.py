"""Zero-private-data tests for retrieval scoring and review-stage smoke."""
import unittest
from metrics_retrieval import summarize_score, summarize_latency, percentile

class MetricContracts(unittest.TestCase):
    def test_neutral_scoring_does_not_hide_real_confusions(self):
        q=[{'qid':'Q001','text':'safe'}]
        g=[{'qid':'Q001','relevant_doc_ids':['A'],'confusable_doc_ids':['B'],
            'neutral_doc_ids':['C'],'type':'person'}]
        m=summarize_score(q,g,{'A','B','C'},{'Q001':['C','B','A']})
        self.assertEqual(m['eligible'],1)
        self.assertEqual(m['hit1'],0)
        self.assertEqual(m['hit5'],1)
        self.assertEqual(m['confusable_hit5'],1)
        self.assertEqual(m['confusable_at5_rate'],1.0)
        self.assertEqual(m['mrr'],.5)

    def test_explicit_denominators(self):
        q=[{'qid':'Q001','text':None},{'qid':'Q002','text':'prepared'},
           {'qid':'Q003','text':'prepared'}]
        g=[{'qid':'Q001','relevant_doc_ids':['A']},
           {'qid':'Q002','relevant_doc_ids':['MISSING']},
           {'qid':'Q003','relevant_doc_ids':['A']}]
        m=summarize_score(q,g,{'A'},{'Q003':['A']})
        self.assertEqual((m['total'],m['deferred'],m['no_indexable_gold'],m['eligible']),
                         (3,1,1,1))
        self.assertEqual(m['hit1_rate'],1.0)
        self.assertIsNone(m['confusable_at5_given_candidate'])

    def test_duplicate_and_unknown_ranking_rejected(self):
        q=[{'qid':'Q001','text':'safe'}]
        g=[{'qid':'Q001','relevant_doc_ids':['A']}]
        with self.assertRaisesRegex(ValueError,'INVALID_RANKED_IDS'):
            summarize_score(q,g,{'A'},{'Q001':['B']})

    def test_latency(self):
        m=summarize_latency([{'total_ms':1.0,'db_ms':.1},
                             {'total_ms':2.0,'db_ms':.3},
                             {'total_ms':4.0,'db_ms':.5}])
        self.assertEqual(m['total_ms']['p95'],4.0)
        self.assertEqual(m['total_ms']['p50'],2.0)
        self.assertEqual(percentile([],95),None)


if __name__ == '__main__':
    unittest.main()
