"""Conservative information triage. Heuristics are NOT truth or fact verification.

Only labels a bounded set of already processed messages. Never deletes records
or changes OpenAI routing. Uncertain cases remain visible under 'other'.
"""
from __future__ import annotations
import re
from .smart_text import search_tokens

# Use whole-word matching with short stem alternatives only where helpful.
_AD_PHRASES = (
    r'\bваканси\w*\b', r'\bищем сотрудник\w*\b',
    r'\bподработк\w*\b', r'\bзарплат\w*\b',
    r'\bзаработок\b', r'\bдоходом?\b',
    r'\bоплатой?\b', r'\bзаказ\w*\b',
    r'\bпрода\w*\b', r'\bкуплю\b',
    r'\bуслуг\w*\b', r'\bреклам\w*\b',
    r'\bставк\w*\b', r'\bказино\b',
    r'\bоформлени\w*\b',
    r'\bскидк\w*\b', r'\bакци\w*\b',
    r'\bбез экзамен\w*\b',
    r'\bсдам квартиру\b',
    r'\bпродается\b',
    r'\bпишите в личк\w*\b',
    r'\bот [0-9]+ ?(руб|доллар|usd|₽|\$)\b',
    r'\b\$[0-9]+\b',
)
_NEWS_PHRASES = (
    r'\bперекры\w*\b', r'\bотключени\w*\b',
    r'\bпожар\w*\b', r'\bавари\w*\b',
    r'\bзадержа\w*\b', r'\bэвакуаци\w*\b',
    r'\bземлетрясени\w*\b', r'\bпротест\w*\b',
    r'\bгранич\w*\b', r'\bграниц\w*\b',
    r'\bводоснабжени\w*\b', r'\bземлетрясение\b',
    r'\broad closed\b', r'\bpower outage\b',
    r'\bearthquake\b', r'\bfire outbreak\b',
    r'հրդեհ', r'երկրաշարժ', r'փակվել', r'անջատում',
)
_AD = [re.compile(x, re.IGNORECASE) for x in _AD_PHRASES]
_NEWS = [re.compile(x, re.IGNORECASE) for x in _NEWS_PHRASES]


def categorize(text: str, classification: dict | None = None) -> tuple[str, list[str]]:
    """Return (candidate|likely_ad|other, non-authoritative reasons).

    Heuristics intentionally require >=2 advertising markers for 'likely_ad'.
    A single strong event indicator can mark candidate; not verification.
    """
    classification = classification if isinstance(classification, dict) else {}
    normalized = ' '.join(search_tokens((text or '')[:5000]))
    ad_hits = sum(bool(pattern.search(normalized)) for pattern in _AD)
    news_hits = sum(bool(pattern.search(normalized)) for pattern in _NEWS)
    if news_hits and ad_hits < 2:
        return 'candidate', ['тематические слова, нужна проверка']
    if ad_hits >= 2 and news_hits == 0:
        return 'likely_ad', ['несколько признаков объявления или рекламы']
    if classification.get('priority') == 'high' and ad_hits < 2:
        return 'candidate', ['высокий приоритет исходной модели']
    if classification.get('relevant') is True and ad_hits == 0:
        return 'candidate', ['тема отмечена моделью как релевантная']
    return 'other', ['неоднозначно; сохранено для просмотра']
