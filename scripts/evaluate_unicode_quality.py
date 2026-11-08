"""Synthetic corpus evaluation: no user text, network, DB or model calls."""
import json
from pathlib import Path
from telegram_monitor.cloud.unicode_context import inspect_unicode,VERSION
CASES=Path(__file__).resolve().parents[1]/"tests/fixtures/unicode_quality_ru_am_en.json"
def evaluate():
    cases=json.loads(CASES.read_text())
    counts={"tp":0,"fp":0,"tn":0,"fn":0};mismatches=[]
    for case in cases:
        report=inspect_unicode(case["input"],enabled=True)
        if not report["available"]:raise RuntimeError("Unicode extra required")
        assert report["original"]==case["input"]
        changed=report["normalized"]!=case["input"]
        counts[("tp" if changed else "fn") if case["positive"] else ("fp" if changed else "tn")]+=1
        if report["normalized"]!=case["expected"]:
            mismatches.append({"id":case["id"],"group":case["group"]})
    tp,fp,fn=counts["tp"],counts["fp"],counts["fn"]
    return {"version":VERSION,"synthetic":True,"cases":len(cases),"counts":counts,
            "precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None,"mismatches":mismatches,
            "limitation":"Curated synthetic corpus; metrics do not estimate real-world accuracy"}
if __name__=="__main__":
    result=evaluate()
    print("UNICODE_QUALITY="+json.dumps(result,sort_keys=True))
    if any(x["group"]=="baseline" for x in result["mismatches"]):raise SystemExit(1)
