import copy
from unittest.mock import patch
import pytest
from telegram_monitor.cloud.variant_search import search_variants

def normalize(text, **kwargs):
    return {"available": True, "normalized": text.replace("пoжар", "пожар")}

def test_originals_untouched_and_match_reason():
    rows=[{"key":"1:1","text":"пoжар","summary":"keep","score":1},{"key":"1:2","text":"пожар"}]
    before=copy.deepcopy(rows)
    with patch("telegram_monitor.cloud.variant_search.inspect_unicode",side_effect=normalize):
        out=search_variants(rows,"пожар",enabled=True)
    assert rows==before
    assert [m["matched_on"] for m in out["matches"]]==["normalized","original"]
    assert out["persisted"] is False

def test_candidate_iterator_is_bounded():
    consumed=[]
    def candidates():
        for i in range(10000):
            consumed.append(i)
            yield {"key":str(i),"text":"пожар"}
    with patch("telegram_monitor.cloud.variant_search.inspect_unicode",side_effect=normalize):
        out=search_variants(candidates(),"пожар",enabled=True)
    assert len(consumed)==101
    assert out["scanned"]==100 and out["candidate_truncated"]
    assert out["scope"]=="provided_candidates"

def test_disabled_and_missing_backend_fail_explicitly():
    with pytest.raises(ValueError):search_variants([],"пожар")
    with patch("telegram_monitor.cloud.variant_search.inspect_unicode",return_value={"available":False}):
        with pytest.raises(RuntimeError):search_variants([],"пожар",enabled=True)

def test_long_text_reports_partial_coverage():
    with patch("telegram_monitor.cloud.variant_search.inspect_unicode",side_effect=normalize):
        out=search_variants([{"key":"1","text":"x"*4001+"пожар"}],"пожар",enabled=True)
    assert out["text_truncated"] and out["matches"]==[]
