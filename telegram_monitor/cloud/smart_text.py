"""Conservative, pure-Python Unicode normalization for read-only discovery.

Do not change the stored original, usernames, or URLs. No external services.
"""
from __future__ import annotations
import re
import unicodedata

# Visually similar *Latin lowercase* glyphs in a token that already contains Cyrillic.
# We deliberately do not transliterate arbitrary English words.
_LOOKALIKE = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м", "t": "т",
})
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_URLS = re.compile(r"(?:https?://|www\.|t\.me/)[^\s]+", re.IGNORECASE)
_HANDLES = re.compile(r"(?<!\w)@[a-zA-Z][a-zA-Z0-9_]{4,}")
_LATIN = re.compile(r"[a-z]")
_CYR = re.compile(r"[а-яё]")

# Exact-place aliases only: not general-purpose Romanized-Russian conversion.
_PLACE_ALIASES = {
    'erevan':'ереван','yerevan':'ереван','erewan':'ереван',
    'gyumri':'гюмри','gumri':'гюмри',
    'armenia':'армения','armeniya':'армения','hayastan':'армения',
    'syunik':'сюник','siunik':'сюник',
    'kapan':'капан','goris':'горис','sevan':'севан',
    'dilijan':'дилижан','dilidjan':'дилижан',
    'vanadzor':'ванадзор','tavush':'тавуш',
}
# A small, reviewed list of common Russian keywords written fully in Latin.
# Do not apply machine transliteration to every English token.
_KEYWORD_ALIASES = {
    'granica': 'граница', 'granitsa': 'граница',
    'granice': 'граница', 'granitse': 'граница',
    'granicy': 'граница', 'protest': 'протест',
    'protesty': 'протест', 'pozhar': 'пожар',
    'novosti': 'новости',
}
_PLACE_INFLECTED = {
    'ереване':'ереван','еревана':'ереван','ереваном':'ереван',
    'армению':'армения','арменией':'армения','армении':'армения',
    'гюмри':'гюмри','сюнике':'сюник','сюника':'сюник',
    'горисе':'горис','гориса':'горис',
    'капане':'капан','капана':'капан',
    'севане':'севан','севана':'севан',
    'дилижане':'дилижан','дилижана':'дилижан',
    'ванадзоре':'ванадзор','ванадзора':'ванадзор',
}
_ARMENIAN_ALIASES = {
    'երևան':'ереван','երեւան':'ереван','գյումրի':'гюмри',
    'հայաստան':'армения','հայաստանի':'армения',
    'սյունիք':'сюник','սյունիքի':'сюник',
    'կապան':'капан','գորիս':'горис','սևան':'севан',
    'սեւան':'севан','դիլիջան':'дилижан','վանաձոր':'ванадзор',
    'տավուշ':'тавуш',
}


def fix_mixed_script(value: str) -> str:
    """Repair simple Latin lookalikes only inside words that already have Cyrillic.

    Non-Cyrillic tokens, including English and Armenian, are preserved.
    """
    cleaned = _INVISIBLE.sub('', unicodedata.normalize('NFKC', value or ''))
    def repair(match):
        token = match.group()
        lower = token.casefold()
        if _CYR.search(lower) and _LATIN.search(lower):
            return lower.translate(_LOOKALIKE)
        return lower
    return _WORDS.sub(repair, cleaned)


def search_tokens(value: str) -> list[str]:
    """Lower-case, remove invisible marks and links, repair mixed scripts.

    Only a handful of unambiguous geographical synonyms are folded.
    Preserve other words as-is to avoid misleading substitutions.
    """
    cleaned = _HANDLES.sub(' ', _URLS.sub(' ', value or ''))
    cleaned = fix_mixed_script(cleaned)
    return [(_PLACE_ALIASES.get(w) or _KEYWORD_ALIASES.get(w) or _PLACE_INFLECTED.get(w) or
             _ARMENIAN_ALIASES.get(w) or w) for w in _WORDS.findall(cleaned)]


def contains_query(text: str, query: str) -> bool:
    terms = search_tokens(query)
    if not terms or len(terms) > 15:
        return False
    words = set(search_tokens(text))
    return all(term in words for term in terms)
