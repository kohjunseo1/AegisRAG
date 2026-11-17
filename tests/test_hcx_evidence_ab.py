"""Evidence-aware HCX A/B tests: same documents, same order, no extra API calls."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"step2_privacy_rag/app"))
from hcx import BASE, OutboundError, attach_evidence_metadata, call_hcx


E='<EMAIL_'+'a'*24+'>'
P='<PERSON_'+'b'*24+'>'
A='<ADDRESS_'+'c'*24+'>'
OTHER='<ADDRESS_'+'d'*24+'>'
DOCS=[
    dict(doc_id="S001",text=f"수신인: {E}\n면접 일정",
         verified_region_match=None),
    dict(doc_id="S002",text=f"주간 VOC: {P}\n처리 완료",
         verified_region_match=None),
]


class EvidenceAwarePayload(unittest.TestCase):
    def invoke(self, mode, question=E, docs=None):
        response=dict(choices=[dict(finish_reason="stop",message=dict(
            content=json.dumps(dict(decision="ANSWER",answer="면접 안내",citations=["S001"]))))],
            usage=dict(prompt_tokens=80,completion_tokens=20))
        vars={"AEGISRAG_V6_EXTERNAL_TRANSFER_APPROVED":"YES","LLM_API_KEY":"synthetic",
              "LLM_API_BASE":BASE,"AEGISRAG_HCX_EVIDENCE_MODE":mode}
        with patch.dict(os.environ,vars),patch("hcx.urllib.request.urlopen") as sender:
            sender.return_value.__enter__.return_value=io.BytesIO(json.dumps(response).encode())
            call_hcx(ROOT,question,docs or DOCS,False)
            payload=json.loads(sender.call_args.args[0].data)
            return payload["messages"][0]["content"],json.loads(payload["messages"][1]["content"])

    def test_baseline_unchanged(self):
        system,user=self.invoke("baseline")
        self.assertNotIn("[근거 활용 보조 규칙]",system)
        self.assertEqual([x["doc_id"] for x in user["documents"]],["S001","S002"])
        self.assertNotIn("query_hmac_matches",user["documents"][0])

    def test_metadata_mode_only_annotations_added(self):
        _,baseline=self.invoke("baseline")
        system,variant=self.invoke("metadata")
        self.assertEqual(system,self.invoke("baseline")[0])
        self.assertEqual([x["doc_id"] for x in variant["documents"]],["S001","S002"])
        for before,after in zip(baseline["documents"],variant["documents"]):
            self.assertEqual({k:v for k,v in after.items()
                              if k not in {"query_hmac_matches","matched_entity_types"}},before)
        self.assertEqual(variant["documents"][0]["query_hmac_matches"],1)
        self.assertEqual(variant["documents"][0]["matched_entity_types"],["EMAIL"])
        self.assertEqual(variant["documents"][1]["query_hmac_matches"],0)

    def test_guidance_preserves_every_candidate(self):
        system,variant=self.invoke("metadata_guidance")
        self.assertIn("[근거 활용 보조 규칙]",system)
        self.assertEqual(len(variant["documents"]),2)
        self.assertEqual(variant["documents"][1]["query_hmac_matches"],0)

    def test_region_without_exact_hmac_does_not_get_removed(self):
        docs=[dict(doc_id="S003",text="지역 우편물 "+OTHER,
                   verified_region_match=True)]
        output=attach_evidence_metadata(A,docs)
        self.assertEqual(output[0]["query_hmac_matches"],0)
        self.assertTrue(output[0]["verified_region_match"])
        self.assertIn(OTHER,output[0]["text"])

    def test_multilabel_entity_types_are_coarse_no_extra_secrets(self):
        docs=[dict(doc_id="S001",text=" ".join([E,P]))]
        output=attach_evidence_metadata(E+" "+P,docs)
        self.assertEqual(output[0]["matched_entity_types"],["EMAIL","PERSON"])
        self.assertEqual(output[0]["query_hmac_matches"],2)
        self.assertNotIn(E,repr(output[0]["matched_entity_types"]))

    def test_invalid_mode_fails_closed(self):
        with patch.dict(os.environ,{"AEGISRAG_HCX_EVIDENCE_MODE":"invalid"}):
            from hcx import evidence_mode
            with self.assertRaisesRegex(OutboundError,"HCX_EVIDENCE_MODE_INVALID"):
                evidence_mode()


if __name__=="__main__":
    unittest.main()
