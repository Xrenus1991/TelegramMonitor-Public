"""Opt-in, bounded news-channel comment ingestion and read-only triage.

User IDs are retained ONLY for conservative direct-threat review candidates,
if Telegram supplies an explicit PeerUser sender. No profiles or member lists.
Raw comment text may itself contain personal details; the UI masks common patterns.
No OpenAI calls are made. Comments are user claims, NOT verified evidence.
Database tables are isolated from the existing message/worker tables.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import re
from sqlalchemy import (MetaData, Table, Column, Index, BigInteger, Integer, String,
                        Text, DateTime, Boolean, select, update, delete, create_engine,
                        event, and_, func, inspect)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .db import messages
from .signal_filter import categorize
from .analytics import place_mentions
from .threat_review import possible_direct_threat, telegram_user_id

comment_md = MetaData()
comments = Table('tm_post_comments', comment_md,
    Column('key', String(140), primary_key=True),
    Column('channel_id', BigInteger, nullable=False),
    Column('post_id', BigInteger, nullable=False),
    Column('discussion_id', BigInteger, nullable=False),
    Column('comment_id', BigInteger, nullable=False),
    Column('timestamp', DateTime(timezone=True), nullable=False),
    Column('text', Text, nullable=False),
    Column('potential_direct_threat', Boolean, nullable=False, default=False),
    Column('author_user_id', BigInteger, nullable=True),
    Column('created_at', DateTime(timezone=True), nullable=False))
Index('ix_tm_comments_post_time', comments.c.channel_id, comments.c.post_id, comments.c.timestamp)
Index('ix_tm_comments_timestamp', comments.c.timestamp)
scans = Table('tm_comment_post_scans', comment_md,
    Column('key', String(100), primary_key=True),
    Column('channel_id', BigInteger, nullable=False),
    Column('post_id', BigInteger, nullable=False),
    Column('scanned_at', DateTime(timezone=True), nullable=False),
    Column('seen', Integer, nullable=False, default=0),
    Column('capped', Boolean, nullable=False, default=False))
Index('ix_tm_comment_scans_channel', scans.c.channel_id, scans.c.scanned_at)

_MASK_EMAIL = re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b')
_MASK_PHONE = re.compile(r'(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)')
_MASK_HANDLE = re.compile(r'(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,}')

def masked(text: str) -> str:
    """Minimal PII masking in API results, not guaranteed anonymity."""
    return _MASK_HANDLE.sub('[HANDLE]', _MASK_PHONE.sub('[PHONE]', _MASK_EMAIL.sub('[EMAIL]', text)))


def channel_ids_from_env(value: str) -> set[int]:
    """Strictly bounded explicit channel allowlist; never wildcard/auto-add."""
    if not value.strip():
        return set()
    chunks = value.split(',')
    if len(chunks) > 100:
        raise ValueError('At most 100 comment channel ids')
    ids = set()
    for piece in chunks:
        piece = piece.strip()
        if not piece.isdecimal() or not 1 <= int(piece) <= 10**18:
            raise ValueError('CLOUD_COMMENTS_CHANNEL_IDS must contain positive numeric channel IDs')
        ids.add(int(piece))
    return ids


def normalise_comment(raw, channel_id: int, post_id: int):
    """Only retain a PeerUser ID for a possible direct-threat review candidate."""
    text = str(getattr(raw, 'message', None) or '').strip()
    peer = getattr(raw, 'peer_id', None)
    discussion_id = getattr(peer, 'channel_id', None)
    msg_id = getattr(raw, 'id', None)
    when = getattr(raw, 'date', None)
    if not text or not isinstance(discussion_id, int) or discussion_id <= 0:
        return None
    if not isinstance(msg_id, int) or msg_id <= 0 or not isinstance(when, datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    flagged = possible_direct_threat(text[:4000])
    return {
        'key': f'{channel_id}:{post_id}:{discussion_id}:{msg_id}',
        'channel_id': channel_id, 'post_id': post_id,
        'discussion_id': discussion_id, 'comment_id': msg_id,
        'timestamp': when.astimezone(timezone.utc), 'text': text[:4000],
        'potential_direct_threat': flagged,
        'author_user_id': telegram_user_id(raw) if flagged else None,
    }


class CommentStore:
    def __init__(self, url, *, initialize=False, read_only=False):
        kwargs = {'pool_pre_ping': True, 'future': True}
        if read_only and str(url).startswith('postgresql'):
            kwargs['connect_args'] = {'options': '-c default_transaction_read_only=on -c statement_timeout=15000'}
        self.engine = create_engine(url, **kwargs)
        self.read_only = read_only
        if read_only and self.engine.dialect.name == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def _sqlite_query_only(dbapi_connection, _record):
                dbapi_connection.execute('PRAGMA query_only=ON')
        if initialize and not read_only:
            comment_md.create_all(self.engine)

    def initialized(self):
        return inspect(self.engine).has_table('tm_post_comments') and inspect(self.engine).has_table('tm_comment_post_scans')

    def choose_posts(self, channel_ids: set[int], *, lookback_hours: int,
                     per_cycle: int, retry_seconds: int) -> list[dict]:
        """Choose recently stored channel posts fairly, preferring unscanned items.

        Each allowed channel contributes at most 2 posts per cycle. For the
        current collection volume this limits Telegram requests and catches
        newly published posts; older threads are checked again on rotation.
        """
        if self.read_only:
            raise RuntimeError('Read-only CommentStore cannot schedule ingestion')
        if not channel_ids or not 1 <= per_cycle <= 100:
            return []
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=lookback_hours)
        candidates = []
        with self.engine.connect() as conn:
            for channel_id in sorted(channel_ids):
                # Latest 60 posts per channel is a deliberate upper bound.
                posts = conn.execute(select(messages.c.message_id, messages.c.timestamp)
                    .where(and_(messages.c.channel_id == channel_id, messages.c.timestamp >= cutoff))
                    .order_by(messages.c.timestamp.desc(), messages.c.message_id.desc())
                    .limit(60)).mappings().all()
                if not posts:
                    continue
                keys = [f'{channel_id}:{int(p["message_id"])}' for p in posts]
                previous = {r['key']: r['scanned_at'] for r in conn.execute(
                    select(scans.c.key, scans.c.scanned_at).where(scans.c.key.in_(keys))).mappings()}
                scored = []
                for post in posts:
                    msg_id = int(post['message_id'])
                    last = previous.get(f'{channel_id}:{msg_id}')
                    if last is not None:
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if last > now - timedelta(seconds=retry_seconds):
                            continue
                    ts = post['timestamp']
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    score = (0, -ts.timestamp(), msg_id) if last is None else (1, last.timestamp(), msg_id)
                    scored.append((score, {'channel_id': channel_id, 'post_id': msg_id}))
                scored.sort(key=lambda item: item[0])
                candidates.extend((score, post) for score, post in scored[:2])
        candidates.sort(key=lambda item: item[0])
        return [post for _score, post in candidates[:per_cycle]]

    def save_thread(self, channel_id: int, post_id: int, rows: list[dict], *, capped: bool) -> int:
        if self.read_only:
            raise RuntimeError('Read-only CommentStore cannot ingest')
        now = datetime.now(timezone.utc)
        scan_key = f'{channel_id}:{post_id}'
        with self.engine.begin() as conn:
            inserted = 0
            for row in rows:
                if row['channel_id'] != channel_id or row['post_id'] != post_id:
                    raise ValueError('Comment parent mismatch')
                clean_row = dict(row)
                clean_row['potential_direct_threat'] = bool(row.get('potential_direct_threat', False))
                clean_row['author_user_id'] = row.get('author_user_id') if clean_row['potential_direct_threat'] else None
                if self.engine.dialect.name == 'postgresql':
                    stmt = pg_insert(comments).values(**clean_row, created_at=now).on_conflict_do_nothing(index_elements=['key'])
                elif self.engine.dialect.name == 'sqlite':
                    stmt = sqlite_insert(comments).values(**clean_row, created_at=now).on_conflict_do_nothing(index_elements=['key'])
                else:
                    raise RuntimeError('Unsupported database')
                # INSERT rowcount can be -1 with psycopg/SQLAlchemy.
                # RETURNING distinguishes insertion from ON CONFLICT reliably.
                key = conn.execute(stmt.returning(comments.c.key)).scalar_one_or_none()
                inserted += int(key is not None)
            values = {'key': scan_key, 'channel_id': channel_id, 'post_id': post_id,
                      'scanned_at': now, 'seen': len(rows), 'capped': bool(capped)}
            if self.engine.dialect.name == 'postgresql':
                stmt = pg_insert(scans).values(**values).on_conflict_do_update(
                    index_elements=['key'], set_={'scanned_at': now, 'seen': len(rows), 'capped': bool(capped)})
            else:
                stmt = sqlite_insert(scans).values(**values).on_conflict_do_update(
                    index_elements=['key'], set_={'scanned_at': now, 'seen': len(rows), 'capped': bool(capped)})
            conn.execute(stmt)
            return inserted

    def prune(self, *, retention_days: int):
        if self.read_only or not 1 <= retention_days <= 365:
            raise ValueError('Invalid comment retention')
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self.engine.begin() as conn:
            removed = conn.execute(delete(comments).where(comments.c.timestamp < cutoff)).rowcount
            conn.execute(delete(scans).where(scans.c.scanned_at < cutoff))
        return removed

    def overview(self, *, hours=72, limit=30):
        if not self.initialized():
            return {'initialized': False, 'threads': [], 'total_comments': 0, 'sample_limited': False}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.engine.connect() as conn:
            grouped = conn.execute(select(comments.c.channel_id, comments.c.post_id,
                func.count().label('n'), func.max(comments.c.timestamp).label('latest'))
                .where(comments.c.timestamp >= cutoff)
                .group_by(comments.c.channel_id, comments.c.post_id)
                .order_by(func.max(comments.c.timestamp).desc())
                .limit(limit + 1)).mappings().all()
            latest = grouped[:limit]
            count = conn.execute(select(func.count()).select_from(comments)
                    .where(comments.c.timestamp >= cutoff)).scalar_one()
            keys = [(x['channel_id'], x['post_id']) for x in latest]
            post_names = {}
            for channel_id, post_id in keys:
                row = conn.execute(select(messages.c.channel_name, messages.c.text)
                    .where(and_(messages.c.channel_id == channel_id, messages.c.message_id == post_id))
                    .limit(1)).first()
                if row:
                    post_names[(channel_id, post_id)] = row
        out = []
        for row in latest:
            key = (row['channel_id'], row['post_id'])
            title = post_names.get(key)
            ts = row['latest']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append({'channel_id': key[0], 'post_id': key[1], 'count': row['n'],
                        'source': masked(str(title[0])[:150]) if title else 'Источник',
                        'post_excerpt': masked(str(title[1])[:220]) if title else '',
                        'last_comment_at': ts.isoformat()})
        return {'initialized': True, 'threads': out, 'total_comments': count,
                'sample_limited': len(grouped) > limit}

    def thread(self, channel_id: int, post_id: int, *, limit=50):
        if not self.initialized():
            return {'initialized': False, 'items': [], 'count': 0}
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(comments)
                .where(and_(comments.c.channel_id == channel_id, comments.c.post_id == post_id))).scalar_one()
            rows = conn.execute(select(comments.c.timestamp, comments.c.text,
                comments.c.potential_direct_threat, comments.c.author_user_id,
                comments.c.discussion_id, comments.c.comment_id)
                .where(and_(comments.c.channel_id == channel_id, comments.c.post_id == post_id))
                .order_by(comments.c.timestamp.desc(), comments.c.comment_id.desc())
                .limit(limit)).mappings().all()
        items = []
        totals = Counter()
        for row in rows:
            text = str(row['text'])
            category, reasons = categorize(text)
            totals[category] += 1
            dt = row['timestamp']
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            items.append({'timestamp': dt.isoformat(), 'excerpt': masked(text[:450]),
                          'possible_direct_threat': bool(row['potential_direct_threat']),
                          'telegram_user_id': row['author_user_id'] if row['potential_direct_threat'] else None,
                          'message_url': f"https://t.me/c/{row['discussion_id']}/{row['comment_id']}" if row['potential_direct_threat'] else None,
                          'category': category, 'reasons': reasons[:3],
                          'places_mentioned': place_mentions(text)[:5]})
        return {'initialized': True, 'channel_id': channel_id, 'post_id': post_id,
                'count': count, 'displayed': len(items), 'sample_limited': count > limit,
                'category_counts_in_displayed': dict(totals), 'items': items,
                'note': 'Возможная прямая угроза — только сигнал для ручной проверки. Telegram User ID доступен лишь при явном PeerUser; подтверждение автора и намерений не выполняется.'}

    def alerts_between(self, begin: datetime, end: datetime, *, limit=30):
        if not self.initialized():
            return {'initialized': False, 'count': 0, 'items': [], 'sample_limited': False}
        if begin.tzinfo is None or end.tzinfo is None or begin >= end:
            raise ValueError('Timezone-aware increasing range required')
        if not 1 <= limit <= 100:
            raise ValueError('Invalid alert limit')
        conditions = and_(comments.c.timestamp >= begin, comments.c.timestamp < end,
                          comments.c.potential_direct_threat.is_(True))
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(comments).where(conditions)).scalar_one()
            rows = conn.execute(select(comments.c.channel_id, comments.c.post_id,
                comments.c.discussion_id, comments.c.comment_id, comments.c.author_user_id,
                comments.c.timestamp, comments.c.text)
                .where(conditions).order_by(comments.c.timestamp.desc(), comments.c.key.desc())
                .limit(limit)).mappings().all()
        items = []
        for r in rows:
            ts = r['timestamp']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            items.append({
                'channel_id': r['channel_id'], 'post_id': r['post_id'],
                'telegram_user_id': r['author_user_id'],
                'message_url': f"https://t.me/c/{r['discussion_id']}/{r['comment_id']}",
                'timestamp': ts.isoformat(), 'excerpt': masked(str(r['text'])[:450]),
                'assessment': 'ПРИЗНАК ПРЯМОЙ УГРОЗЫ — ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ',
            })
        return {'initialized': True, 'count': count, 'items': items,
                'sample_limited': count > limit,
                'note': 'Эвристический сигнал не доказывает угрозу, личность, вину или намерения автора.'}

    def dispose(self):
        self.engine.dispose()



async def collect_thread(client, entity, store: CommentStore, post_id: int, *, max_comments: int):
    """Collect comments only when Telegram reports an available thread.

    A missing/empty comment section is a successful zero-result check.
    Telegram errors during actual fetching still propagate for retry.
    """
    channel_id = int(entity.id)

    post = await client.get_messages(entity, ids=post_id)

    if post is None:
        raise ValueError("Channel post no longer accessible")

    reply_info = getattr(post, "replies", None)

    if (
        reply_info is None
        or not getattr(reply_info, "comments", False)
        or int(getattr(reply_info, "replies", 0) or 0) <= 0
    ):
        store.save_thread(channel_id, post_id, [], capped=False)
        return 0, 0

    raw_count = 0
    results = []

    async for comment in client.iter_messages(
        entity, reply_to=post_id, limit=max_comments
    ):
        raw_count += 1
        record = normalise_comment(comment, channel_id, post_id)
        if record is not None:
            results.append(record)

    added = store.save_thread(
        channel_id, post_id, results,
        capped=raw_count >= max_comments
    )
    return added, raw_count
