"""Conservative mixed Latin/Cyrillic token detection for moderation signals.

Pure stdlib; never modifies the original message. A mixed-script token is a
review signal, not proof of malicious intent.
"""
from __future__ import annotations

import unicodedata


def mixed_latin_cyrillic_tokens(text: str) -> list[str]:
    """Return distinct tokens containing both Latin and Cyrillic letters.

    Preserve first-seen order and original spelling. Combining marks are
    allowed inside tokens but do not determine script membership.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    found: list[str] = []
    seen: set[str] = set()
    chars: list[str] = []

    def flush() -> None:
        if not chars:
            return
        token = "".join(chars)
        chars.clear()
        scripts = set()
        for char in token:
            if not char.isalpha():
                continue
            # Compatibility-styled Latin letters (e.g. mathematical bold)
            # retain their original form in evidence but must still count
            # as Latin for the mixed-script review signal.
            for normalized in unicodedata.normalize("NFKC", char):
                if not normalized.isalpha():
                    continue
                name = unicodedata.name(normalized, "")
                if name.startswith("LATIN "):
                    scripts.add("latin")
                elif name.startswith("CYRILLIC "):
                    scripts.add("cyrillic")
        if len(scripts) == 2 and token not in seen:
            seen.add(token)
            found.append(token)

    for char in text:
        category = unicodedata.category(char)
        if char.isalpha() or (chars and category.startswith(("M", "Cf"))) or (category == "Cf" and not chars):
            # Keep leading invisible format characters with the following
            # word, rather than losing evidence when the token starts.

            # Invisible format characters (e.g. zero-width joiners) can be
            # inserted to hide script mixing. Preserve them in the reported
            # token, but never let them establish script membership.
            chars.append(char)
        else:
            flush()
    flush()
    return found
