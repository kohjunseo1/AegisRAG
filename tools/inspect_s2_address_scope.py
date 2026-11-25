"""Inspect A187 address annotation scope. Aggregate only, no inference/network.
Region matches are heuristic structural observations, not authoritative labels.
"""
from collections import Counter
from pathlib import Path
import json
from starter.s2_query_controller import region_paths
from tools.benchmark_step1 import load_data

def main():
    p=Path.home()/"AegisRAG/step2_privacy_rag/local_eval/holdout_labeled.jsonl"
    docs=load_data("A187",p)
    c=Counter()
    for d in docs:
        text=d["text"];addresses=[e for e in d["entities"] if e["label"]=="address"]
        c["address_gold_spans"]+=len(addresses)
        for e in addresses:
            value=text[e["start"]:e["end"]].strip()
            paths=region_paths(value)
            if any(a==0 and b==len(value) for a,b,_ in paths):c["gold_region_only"]+=1
            elif any(a==0 for a,b,_ in paths):c["gold_region_plus_tail"]+=1
            else:c["gold_other_structure"]+=1
        for a,b,_ in region_paths(text):
            c["region_pattern_occurrences"]+=1
            if any(e["start"]<=a and b<=e["end"] for e in addresses):c["region_inside_gold_address"]+=1
            elif any(a<e["end"] and e["start"]<b for e in addresses):c["region_partly_inside_gold_address"]+=1
            else:c["region_outside_gold_address"]+=1
    print("ADDRESS_GOLD_SCOPE",json.dumps(dict(c),ensure_ascii=False),flush=True)
if __name__=="__main__":main()
