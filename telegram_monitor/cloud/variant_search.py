"""Bounded, read-only matching over an explicitly selected candidate window."""
from itertools import islice
from .unicode_context import inspect_unicode, VERSION, MAX_CHARS

MAX_CANDIDATES = 100

def search_variants(rows, query, *, enabled=False):
    if not enabled:
        raise ValueError("variant search disabled")
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        raise ValueError("query must contain 2-200 characters")
    probe = inspect_unicode(query.strip(), enabled=True)
    if not probe["available"]:
        raise RuntimeError("Unicode backend unavailable")
    needle = probe["normalized"].casefold()
    selected = list(islice(iter(rows), MAX_CANDIDATES + 1))
    matches = []
    text_truncated = False
    for row in selected[:MAX_CANDIDATES]:
        original = row.get("text") or ""
        if not isinstance(original, str):
            raise TypeError("candidate text must be a string")
        # Keep one extra character so the normalizer can detect a cut token.
        result = inspect_unicode(original[:MAX_CHARS + 1], enabled=True)
        if not result["available"]:
            raise RuntimeError("Unicode backend unavailable")
        text_truncated |= len(original) > MAX_CHARS
        normalized = result["normalized"][:MAX_CHARS]
        if needle in normalized.casefold():
            matches.append({"key": row["key"], "matched_on": "original" if needle in original[:MAX_CHARS].casefold() else "normalized",
                            "version": VERSION})
    return {"matches": matches, "scanned": min(len(selected), MAX_CANDIDATES),
            "candidate_limit": MAX_CANDIDATES, "candidate_truncated": len(selected) > MAX_CANDIDATES,
            "text_truncated": text_truncated, "scope": "provided_candidates",
            "version": VERSION, "persisted": False}
