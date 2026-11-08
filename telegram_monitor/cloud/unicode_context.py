"""Versioned, bounded Unicode suggestions. Stored originals are never modified."""
import re

VERSION = "ru-context-v2"
MAX_CHARS = 4000
# Protected spans are retained byte-for-byte, including mixed-script identifiers.
_PROTECTED = re.compile(r"(?<!\w)urn:\S+|(?<!\w)\w+_\w+|(?:https?://|www\.|t\.me/)\S+|(?<!\w)@[\w.]+|[\w.+-]+@[\w.-]+\.\w+|(?<!\w)[\w-]+\.(?:com|org|net|ru|am)(?:/\S*)?", re.I)
_TOKENS = re.compile(r"[^\W\d_]+(?:[\u200b\u2060\ufeff][^\W\d_]+)*", re.UNICODE)
_CYR = re.compile(r"[А-Яа-яЁё]")
# Only these reviewed words may receive a normalized suggestion.
_REVIEWED = frozenset("граница границе границу границы пожар пожара пожаре протест протесты угроза угрозы угрожает взрыв взрыва взрыве ереван ереване армения армении".split())
_IGNORABLE = frozenset("\u200b\u2060\ufeff")


def _backend():
    try:
        from confusable_homoglyphs import confusables
        return confusables
    except ImportError:
        return None


def inspect_unicode(value, *, enabled=False):
    if not isinstance(value, str):
        raise TypeError("text must be a string")
    result = {"version": VERSION, "original": value, "normalized": value,
              "enabled": enabled, "available": False, "findings": [],
              "truncated": len(value) > MAX_CHARS}
    if not enabled:
        return result
    backend = _backend()
    if backend is None:
        return result
    result["available"] = True
    protected = [m.span() for m in _PROTECTED.finditer(value[:MAX_CHARS])]
    findings = []
    for match in _TOKENS.finditer(value[:MAX_CHARS]):
        start, end = match.span()
        # Never treat a partial token at the processing boundary as a whole word.
        if end == MAX_CHARS and len(value) > MAX_CHARS:
            continue
        if any(start < right and end > left for left, right in protected):
            continue
        token = match.group()
        if len(token) > 64 or len(_CYR.findall(token)) < 2:
            continue
        output = []
        changed = False
        failed = False
        for character in token.casefold():
            if character in _IGNORABLE:
                changed = True
                continue
            if _CYR.fullmatch(character):
                output.append(character)
                continue
            candidates = backend.is_confusable(character, preferred_aliases=["cyrillic"]) or []
            replacements = {item["c"].casefold()
                            for candidate in candidates
                            for item in candidate["homoglyphs"]
                            if len(item["c"]) == 1 and _CYR.fullmatch(item["c"])}
            # Unicode tables may connect Greek -> Latin -> Cyrillic rather
            # than listing a direct pair. Allow only a small Latin bridge and
            # still require a unique Cyrillic result plus reviewed whole word.
            if not replacements and "\u0370" <= character <= "\u03ff":
                bridges = backend.is_confusable(character, greedy=True) or []
                latin = {item["c"] for candidate in bridges
                         for item in candidate["homoglyphs"]
                         if len(item["c"]) == 1 and item["c"] in "aceopxy"}
                for glyph in latin:
                    for candidate in backend.is_confusable(glyph, preferred_aliases=["cyrillic"]) or []:
                        replacements.update(item["c"].casefold() for item in candidate["homoglyphs"]
                                            if len(item["c"]) == 1 and _CYR.fullmatch(item["c"]))
            if len(replacements) != 1:
                failed = True
                break
            output.append(replacements.pop())
            changed = True
        suggestion = "".join(output)
        if not failed and changed and suggestion in _REVIEWED:
            findings.append({"start": start, "end": end, "suggestion": suggestion,
                             "kind": "contextual_unicode_variant"})
    text = value
    for finding in reversed(findings):
        text = text[:finding["start"]] + finding["suggestion"] + text[finding["end"]:]
    result.update(normalized=text, findings=findings)
    return result
