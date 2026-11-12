"""No-DB synthetic proof that 177 baseline + 10 human reviews build 187 docs."""
import hashlib
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"scripts"),str(ROOT/"step2_privacy_rag/app")]
from q001_human_assisted_e2e import build_in_memory,IDS
from review_dashboard_q001 import get_cases
from dataset_preparation import key_tag

KEY=b"review-e2e-synthetic-fixed-key-at-least-32-bytes"


class Detector:
    def detect(self,text):
        marker="장인" if text.startswith("[부고]") else "오가은"
        start=text.index(marker);end=start+len(marker)
        return {"status":"READY_FOR_PSEUDONYMIZATION" if text.startswith("[부고]")
                else "REVIEW_REQUIRED",
                "issues":[] if text.startswith("[부고]") else
                [{"code":"possible_uncovered_name_suffix"}],
                "entities":[{"label":"person","start":start,"end":end}],
                "mask_intervals":[{"start":start,"end":end}]}
    def ax_predict(self,text):
        return []
    def veil_predict(self,text):
        if text.startswith("[부고]"):
            start=text.index("장인상")
            return [{"label":"person","start":start,"end":start+3}]
        return []


class BatchContract(unittest.TestCase):
    def test_ten_human_reviews_generate_187_masked_docs_with_no_baseline_change(self):
        baseline={
            "format":"aegisrag-local-embedding-eval-v1",
            "hmac_key_tag":key_tag(KEY),
            "documents":[{"doc_id":f"D{i:03}","text":f"기존 마스킹 문서 {i}",
                          "region_keys":[]} for i in range(177)],
            "queries":[{"qid":f"Q{i:03}","text":"일반 질문","region_keys":[]}
                       for i in range(88)],
            "gold_for_evaluation_only":[{"qid":f"Q{i:03}",
                    "relevant_doc_ids":["D000"]} for i in range(88)],
            "counts":{"query_states":{"LOCAL_PREPARED":88}}
        }
        sources={did:("[부고] 장인상 안내" if did=="H0091"
                      else "오가은이 상담했습니다.") for did in IDS}
        detector=Detector()
        cases=get_cases(sources,detector)
        events=[]
        for did in IDS:
            target=cases[did]["candidates"][0]
            choice=("NON_PERSON_EXPRESSION" if did=="H0091"
                    else "SHORT_NAME_PLUS_PARTICLE")
            events.append({
                "format":"q001-review-draft-v1",
                "document_id":did,"candidate_index":0,
                "kind":target["kind"],
                "source_span":[target["start"],target["end"]],
                "source_digest":hashlib.sha256(sources[did].encode()).hexdigest(),
                "human_classification":choice,
                "state":"NEEDS_INDEPENDENT_REVALIDATION"})
        result,report=build_in_memory(baseline,sources,detector,KEY,events)
        self.assertEqual(len(result["documents"]),187)
        self.assertEqual(report["review_kind_counts"],{"name":9,"veil":1})
        self.assertTrue(report["baseline_documents_unchanged"])
        self.assertTrue(report["mask_coverage_non_decreasing"])
        self.assertTrue(report["all88_queries_unchanged"])
        self.assertFalse(report["db_written"])
        revised={x["doc_id"]:x["text"] for x in result["documents"] if x["doc_id"] in IDS}
        self.assertNotIn("오가은이",revised["H0002"])
        self.assertNotIn("장인상",revised["H0091"])
        self.assertNotIn("<PERSON_",revised["H0091"])
        self.assertIn("<PERSON_",revised["H0002"])
        self.assertEqual(len(baseline["documents"]),177)

    def test_incomplete_review_batch_never_creates_187_docs(self):
        baseline={
            "format":"aegisrag-local-embedding-eval-v1","hmac_key_tag":key_tag(KEY),
            "documents":[{"doc_id":f"D{i:03}","text":"unchanged",
                          "region_keys":[]} for i in range(177)],
            "queries":[{"qid":f"Q{i:03}","text":None,"region_keys":[]} for i in range(88)],
            "gold_for_evaluation_only":[{"qid":f"Q{i:03}",
                                          "relevant_doc_ids":["D000"]} for i in range(88)],
        }
        sources={did:("[부고] 장인상 안내" if did=="H0091" else "오가은이 상담했습니다.")
                 for did in IDS}
        from review_apply_q001 import ReviewValidationError
        with self.assertRaises(ReviewValidationError):
            build_in_memory(baseline,sources,Detector(),KEY,[])


if __name__=="__main__":
    unittest.main()
