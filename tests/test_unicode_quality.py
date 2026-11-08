import json
from pathlib import Path
import pytest
from telegram_monitor.cloud.unicode_context import inspect_unicode
CASES=json.loads((Path(__file__).parent/"fixtures/unicode_quality_ru_am_en.json").read_text())
@pytest.mark.parametrize("case",[c for c in CASES if c["group"]=="baseline"],ids=lambda c:c["id"])
def test_reviewed_multilingual_corpus(case):
    pytest.importorskip("confusable_homoglyphs")
    report=inspect_unicode(case["input"],enabled=True)
    assert report["available"] and report["original"]==case["input"]
    assert report["normalized"]==case["expected"]
