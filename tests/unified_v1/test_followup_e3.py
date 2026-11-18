from dataclasses import replace
import unittest
from unified_v1.contracts import PersonMention,ContractError
from unified_v1.pipeline import query_from_mentions
from unified_v1.retrieval import IndexedDocument
from unified_v1.generation import ContextBuilder
from test_p4 import approved,index,TOKEN1,ENTITY1,ENTITY2
from test_p5 import guard

class NameLookupTests(unittest.TestCase):
    def test_name_only_candidate_search_and_actual_ambiguity(self):
        p,a=approved(TOKEN1,'query')
        mention=PersonMention('m','q','n_'+'a'*24,(),('NAME_ONLY',))
        query=query_from_mentions(p,a,[mention])
        self.assertTrue(query.lookup_only);self.assertFalse(query.identity_unresolved)
        self.assertFalse(query.entity_ids)
        ix=index();d,da=approved(TOKEN1+' 합성 절차 안내')
        ix.add(IndexedDocument('d1',d,da,entity_ids=(ENTITY1,),name_lookup_keys=(mention.name_lookup_key,)))
        rows=ix.retrieve(query)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].identity_match,'weak')
        builder=ContextBuilder(guard(),key_tag=ix.key_tag,policy_version=ix.policy_version,index_version=ix.index_version,corpus_id=ix.corpus_id)
        self.assertTrue(builder.build(query,rows))
        ix.add(IndexedDocument('d2',d,da,entity_ids=(ENTITY2,),name_lookup_keys=(mention.name_lookup_key,)))
        rows=ix.retrieve(query)
        self.assertEqual(len(rows),2) # lookup returns candidates; it never asserts identity
        with self.assertRaisesRegex(ContractError,'CLARIFY_AMBIGUOUS'):builder.build(query,rows)

    def test_attribute_conflict_still_requires_clarification(self):
        p,a=approved(TOKEN1,'query')
        m=PersonMention('m','q','n_'+'a'*24,('shared-contact',),('UNVERIFIED_OWNER',))
        query=query_from_mentions(p,a,[m]);self.assertTrue(query.identity_unresolved)
        with self.assertRaises(ContractError):index().retrieve(query)
