"""Conservative lexical review of explicit direct physical threat statements.

Signals are NOT proof of criminal intent, authorship beyond Telegram metadata, or
actual danger. No person scoring, watchlists, or cross-post profiling.
"""
from __future__ import annotations
import re
from .smart_text import fix_mixed_script

# Deliberately narrow: explicit first-person Russian verb forms or comparable
# English direct statements. Third-party reports ('он угрожал') do not qualify.
_RU_DIRECT = re.compile(r"(?<![\w])(?:убью|зарежу|застрелю|взорву|подожгу)(?![\w])", re.IGNORECASE)
_RU_PLURAL = re.compile(r"(?<![\w])мы\s+(?:убьем|взорвем|подожжем|застрелим)(?![\w])", re.IGNORECASE)
_EN_DIRECT = re.compile(r"\b(?:i\s+(?:will|am\s+going\s+to)|i[’']ll|we\s+will)\s+(?:kill|shoot|bomb|burn)\b", re.IGNORECASE)
_NEGATION = re.compile(r"(?:\bне\s+|\bникого\s+не\s+)$", re.IGNORECASE)


def possible_direct_threat(text: str) -> bool:
    """Return a *manual-review candidate*, never a conclusion about a person.

    Conservative by design: false positives (quotes, jokes) remain possible;
    false negatives (slang, context, Armenian) are also expected.
    """
    normalized = fix_mixed_script(text or '').casefold().replace('ё', 'е')
    for expression in (_RU_DIRECT, _RU_PLURAL, _EN_DIRECT):
        for match in expression.finditer(normalized):
            prefix = normalized[max(0,match.start()-18):match.start()]
            if not _NEGATION.search(prefix):
                return True
    return False


def telegram_user_id(raw) -> int | None:
    """Return only an explicit PeerUser ID, never channel/anonymous admin IDs.

    sender_id by itself is ambiguous: Telegram may present a channel as sender.
    """
    peer = getattr(raw, 'from_id', None)
    user_id = getattr(peer, 'user_id', None)
    if isinstance(user_id, int) and not isinstance(user_id, bool) and 0 < user_id < 10**18:
        return user_id
    return None
