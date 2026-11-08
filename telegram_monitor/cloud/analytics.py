"""Bounded, deterministic read-only analysis of already-classified Telegram posts.

This module DOES NOT certify events or sources. It groups near-identical post
texts conservatively. Independent publications are not independent evidence.
There are no OpenAI calls, migrations or writes to any database table.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
from sqlalchemy import and_, select
from .db import messages
from .smart_text import fix_mixed_script, search_tokens
from .signal_filter import categorize

_URL = re.compile(r"(?:https?://|www\.|t\.me/)[^\s]+", re.IGNORECASE)
_HANDLE = re.compile(r"(?<!\w)@[a-zA-Z][a-zA-Z0-9_]{4,}")
_SPACES = re.compile(r"\s+")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_MEDIA_ONLY = "[МЕДИА БЕЗ ТЕКСТОВОЙ ПОДПИСИ — содержимое не изучено]"

# Literal place-name mentions, NOT confirmed event locations or geocoding.
_PLACE_FORMS = {
    "Ереван": ("ереван", "еревана", "ереване", "ереваном", "երևան", "երեւան"),
    "Гюмри": ("гюмри", "գյումրի"),
    "Ванадзор": ("ванадзор", "ванадзора", "ванадзоре", "վանաձոր"),
    "Капан": ("капан", "капана", "капане", "կապան"),
    "Горис": ("горис", "гориса", "горисе", "գորիս"),
    "Сисиан": ("сисиан", "сисиана", "сисиане", "սիսիան"),
    "Мегри": ("мегри", "մեղրի"),
    "Дилижан": ("дилижан", "дилижана", "дилижане", "դիլիջան"),
    "Иджеван": ("иджеван", "иджевана", "иджеване", "իջևան", "իջեւան"),
    "Севан": ("севан", "севана", "севане", "սևան", "սեւան"),
    "Арташат": ("арташат", "арташата", "арташате", "արտաշատ"),
    "Армавир": ("армавир", "армавира", "армавире", "արմավիր"),
    "Раздан": ("раздан", "раздана", "раздане", "հրազդան"),
    "Аштарак": ("аштарак", "аштарака", "аштараке", "աշտարակ"),
}
_PLACE_WORDS = {form: city for city, forms in _PLACE_FORMS.items() for form in forms}


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _obj(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def text_key(value: str) -> str:
    """Canonicalize text while preserving numbers and place names."""
    value = _URL.sub(" ", value or "")
    value = _HANDLE.sub(" ", value)
    return " ".join(_WORDS.findall(fix_mixed_script(value)))[:3000]


def place_mentions(value: str) -> list[str]:
    words = set(search_tokens(value))
    canonical = {"ереван":"Ереван","гюмри":"Гюмри","капан":"Капан",
                 "горис":"Горис","севан":"Севан","дилижан":"Дилижан",
                 "ванадзор":"Ванадзор"}
    cities = {_PLACE_WORDS[w] for w in words if w in _PLACE_WORDS}
    cities.update(canonical[w] for w in words if w in canonical)
    return sorted(cities)[:10]


def fetch_processed(engine, start: datetime, end: datetime, *, cap: int):
    """Read at most cap rows plus one sentinel; SELECT only, never full-text scan."""
    if cap < 1 or cap > 2000 or start >= end:
        raise ValueError("Invalid analytics bounds")
    with engine.connect() as conn:
        rows = conn.execute(select(
            messages.c.key, messages.c.channel_id, messages.c.channel_name,
            messages.c.message_id, messages.c.sender_user_id,
            messages.c.timestamp, messages.c.text, messages.c.classification,
        ).where(and_(messages.c.state == "done", messages.c.timestamp >= start,
                     messages.c.timestamp < end))
         .order_by(messages.c.timestamp.desc(), messages.c.key.desc())
         .limit(cap + 1)).mappings().all()
    return rows[:cap], len(rows) > cap


def _candidate(row):
    text = str(row["text"] or "")
    canonical = text_key(text)
    classification = _obj(row["classification"])
    summary = classification.get("summary_ru")
    if not isinstance(summary, str) or not summary.strip():
        summary = text
    def short(v, length):
        return _SPACES.sub(" ", v).strip()[:length]
    topic = classification.get("topic")
    priority = classification.get("priority")
    return {
        "key": str(row["key"]), "channel_id": int(row["channel_id"]),
        "sender_user_id": row.get("sender_user_id"),
        "source": str(row["channel_name"])[:250],
        "timestamp": _utc(row["timestamp"]), "summary": short(summary, 460),
        "topic": topic[:100] if isinstance(topic, str) else None,
        "priority": priority if isinstance(priority, str) and priority in {"high", "medium", "low"} else None,
        "relevant": classification.get("relevant") is True,
        "places": place_mentions(text),
        "canonical": canonical, "tokens": set(canonical.split()),
        "numbers": set(re.findall(r"\b\d+\b", canonical)),
        "groupable": (len(canonical) >= 35 and len(canonical.split()) >= 6
                      and text.strip() != _MEDIA_ONLY),
    }


def _similar(a, b):
    if not (a["groupable"] and b["groupable"]):
        return False
    if a["canonical"] == b["canonical"]:
        return True
    if a["numbers"] != b["numbers"]:
        return False
    a_str, b_str = a["canonical"], b["canonical"]
    if min(len(a_str), len(b_str)) / max(len(a_str), len(b_str)) < .88:
        return False
    overlap = len(a["tokens"] & b["tokens"])
    union = len(a["tokens"] | b["tokens"])
    if not union or overlap / union < .87:
        return False
    return SequenceMatcher(None, a_str, b_str, autojunk=False).ratio() >= .92


def _public_item(item):
    return {k: item[k] for k in ("key", "source", "sender_user_id", "summary", "topic", "priority", "places")} | {
        "timestamp": item["timestamp"].isoformat()
    }


def cluster_posts(rows, *, max_gap_hours=24):
    """Group exact/very-near textual copies within a limited time window.

    This is NOT semantic event detection: paraphrases may remain separate.
    """
    items = [_candidate(row) for row in rows]
    clusters = []
    # Indexed by token to reduce comparisons for high-volume sources.
    index = defaultdict(set)
    exact = defaultdict(list)
    for item in items:
        matched = None
        if item["groupable"]:
            for idx in exact[item["canonical"]]:
                lead = clusters[idx][0]
                if abs((item["timestamp"] - lead["timestamp"]).total_seconds()) <= max_gap_hours * 3600:
                    matched = idx
                    break
            if matched is None:
                counts = Counter()
                for token in item["tokens"]:
                    if len(token) >= 5:
                        counts.update(index[token])
                for idx, common in counts.most_common(45):
                    lead = clusters[idx][0]
                    if common < 4:
                        break
                    if abs((item["timestamp"] - lead["timestamp"]).total_seconds()) > max_gap_hours * 3600:
                        continue
                    if _similar(item, lead):
                        matched = idx
                        break
        if matched is None:
            matched = len(clusters)
            clusters.append([])
            if item["groupable"]:
                for token in item["tokens"]:
                    if len(token) >= 5:
                        index[token].add(matched)
                exact[item["canonical"]].append(matched)
        clusters[matched].append(item)
    return clusters


def _cluster_record(group):
    lead = group[0]
    sources = {item["channel_id"] for item in group}
    places = sorted({place for item in group for place in item["places"]})[:10]
    return {
        "id": hashlib.sha256(lead["key"].encode()).hexdigest()[:16],
        "summary": lead["summary"], "first_seen": min(x["timestamp"] for x in group).isoformat(),
        "last_seen": max(x["timestamp"] for x in group).isoformat(),
        "count": len(group), "distinct_sources": len(sources),
        "places_mentioned": places,
        "messages": [_public_item(item) for item in group[:8]],
        "messages_truncated": len(group) > 8,
    }


def duplicate_summary(rows, truncated, *, hours):
    groups = cluster_posts(rows)
    repeated = [g for g in groups if len(g) > 1]
    repeated.sort(key=lambda g: (len(g), g[0]["timestamp"]), reverse=True)
    return {
        "window_hours": hours, "scanned_messages": len(rows),
        "sample_truncated": truncated, "possible_repeat_groups": len(repeated),
        "possible_repeat_messages": sum(len(g) - 1 for g in repeated),
        "items": [_cluster_record(g) for g in repeated[:40]],
        "groups_truncated": len(repeated) > 40,
        "note": "Возможные текстовые повторы; совпадение нескольких каналов не подтверждает факт события."
    }


def daily_brief(rows, truncated, *, day: date):
    groups = cluster_posts(rows)
    processed = [_candidate(row) for row in rows]
    topics = Counter(item["topic"] for item in processed if item["topic"])
    priorities = Counter(item["priority"] for item in processed if item["priority"])
    # Ranking prioritizes model-relevant/high-priority publications, not truth.
    def rank(group):
        p = max(({"high": 3, "medium": 2, "low": 1}.get(x["priority"], 0) for x in group), default=0)
        return (p, int(any(x["relevant"] for x in group)), len(group), group[0]["timestamp"])
    relevant_groups = sorted(groups, key=rank, reverse=True)
    return {
        "date_utc": day.isoformat(), "processed_in_sample": len(rows),
        "sample_truncated": truncated,
        "distinct_sources": len({item["channel_id"] for item in processed}),
        "priority_counts": {p: priorities[p] for p in ("high", "medium", "low")},
        "top_topics": [{"topic": k, "count": v} for k, v in topics.most_common(8)],
        "possible_repeat_groups": sum(len(g) > 1 for g in groups),
        "top_publications": [_cluster_record(group) for group in relevant_groups[:12]],
        "note": "Статистическая сводка по обработанным публикациям UTC; не является независимой проверкой событий и не создаётся моделью."
    }


def utc_day_bounds(day: date):
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def signal_summary(rows, truncated, *, hours: int, category: str = "candidate", query: str = "") -> dict:
    """Sample-bounded triage and spoof-aware search; read-only and auditable.

    Output isn't fact-checked and all counts refer to the last <=1000 posts.
    """
    from .smart_text import contains_query
    if category not in {'candidate', 'likely_ad', 'other', 'all'}:
        raise ValueError('Unknown category')
    if len(query) > 160:
        raise ValueError('Query too long')
    counts = Counter()
    selected = []
    matched = 0
    for raw in rows:
        item = _candidate(raw)
        label, reasons = categorize(str(raw['text'] or ''), _obj(raw['classification']))
        counts[label] += 1
        if query and not contains_query(str(raw['text'] or ''), query):
            continue
        if category != 'all' and category != label:
            continue
        matched += 1
        if len(selected) >= 50:
            continue
        selected.append(_public_item(item) | {
            'category': label, 'reasons': reasons,
            'places_mentioned': item['places'],
        })
    return {
        'window_hours': hours,
        'scanned_messages': len(rows),
        'sample_truncated': truncated,
        'category': category, 'query': query,
        'category_counts': {name: counts[name] for name in ('candidate', 'likely_ad', 'other')},
        'matched_count': matched, 'items_truncated': matched > len(selected),
        'items': selected,
        'note': ('Эвристическая сортировка по уже обработанным сообщениям. '
                 'Не проверяет факты, не удаляет сообщения; статистика только по ограниченной выборке.'),
    }
