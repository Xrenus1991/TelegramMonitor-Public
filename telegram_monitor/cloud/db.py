"""PostgreSQL durable inbox and task/usage ledger; SQLite supported for offline tests.

Transactional invariant: a Telegram checkpoint is advanced only in the same
transaction that saves all successfully fetched messages through that ID.
"""
from datetime import datetime, timedelta, timezone
import base64
import binascii
import json
from uuid import uuid4
from sqlalchemy import (
    MetaData, Table, Column, Index, String, Text, Integer, BigInteger, DateTime,
    select, insert, update, func, and_, or_, create_engine, cast, case, event,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

md = MetaData()
messages = Table("messages", md,
    Column("key", String(100), primary_key=True),
    Column("channel_id", BigInteger, nullable=False),
    Column("message_id", BigInteger, nullable=False),
    Column("sender_user_id", BigInteger, nullable=True),
    Column("channel_name", Text, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("text", Text, nullable=False),
    Column("state", String(20), nullable=False, default="pending"),
    Column("tries", Integer, nullable=False, default=0),
    Column("deep_tries", Integer, nullable=False, default=0),
    Column("lease_until", DateTime(timezone=True)),
    Column("retry_after", DateTime(timezone=True)),
    Column("claim_id", String(40)),
    Column("classification", Text),
    Column("deep_state", String(20), nullable=False, default="skip"),
    Column("deep_analysis", Text),
    Column("deep_completed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_messages_state_time", messages.c.state, messages.c.retry_after, messages.c.timestamp)
Index("ix_messages_deep_time", messages.c.deep_state, messages.c.retry_after, messages.c.timestamp)
checkpoints = Table("checkpoints", md,
    Column("channel_id", String(40), primary_key=True),
    Column("message_id", BigInteger, nullable=False),
)
budgets = Table("budgets", md,
    Column("month", String(7), primary_key=True),
    Column("used_micro_usd", BigInteger, nullable=False, default=0),
)
requests = Table("requests", md,
    Column("id", String(40), primary_key=True),
    Column("month", String(7), nullable=False),
    Column("model", String(100), nullable=False),
    Column("state", String(20), nullable=False),
    Column("reserved_micro_usd", BigInteger, nullable=False),
    Column("charged_micro_usd", BigInteger),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("at", DateTime(timezone=True), nullable=False),
)


def now():
    return datetime.now(timezone.utc)


def _insert_ignore(conn, table, values, keys):
    dialect = conn.engine.dialect.name

    if dialect == "postgresql":
        stmt = pg_insert(table).values(**values)
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)

    elif dialect == "sqlite":
        stmt = sqlite_insert(table).values(**values)
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)

    else:
        raise RuntimeError("Only PostgreSQL and SQLite are supported")

    stmt = stmt.returning(keys[0])
    result = conn.execute(stmt)

    return 1 if result.scalar_one_or_none() is not None else 0



class Store:
    def __init__(self, url: str, *, read_only: bool = False):
        """Writable legacy Store for collector/worker; read-only connection for Dashboard.

        A read-only Store skips schema creation and migrations entirely. PostgreSQL
        sessions are additionally made transaction-read-only by the server, while
        SQLite sessions enable PRAGMA query_only for the test/offline backend.
        For least privilege in production, also use a PostgreSQL SELECT-only role.
        """
        self.read_only = read_only
        kwargs = {"pool_pre_ping": True, "future": True}
        if read_only and url.startswith("postgresql"):
            kwargs["connect_args"] = {"options": "-c default_transaction_read_only=on -c statement_timeout=15000"}
        self.engine = create_engine(url, **kwargs)
        if read_only:
            if self.engine.dialect.name == "sqlite":
                @event.listens_for(self.engine, "connect")
                def _sqlite_read_only(dbapi_connection, connection_record):
                    dbapi_connection.execute("PRAGMA query_only=ON")
            return
        md.create_all(self.engine)
        # RC1 -> RC2 additive migration. create_all() alone does NOT upgrade existing tables.
        from sqlalchemy import inspect
        if "deep_tries" not in {c["name"] for c in inspect(self.engine).get_columns("messages")}:
            with self.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN deep_tries INTEGER NOT NULL DEFAULT 0")
        # Additive migration for established installations. No historical sender
        # information can be inferred from messages already stored as plain text.
        if "sender_user_id" not in {c["name"] for c in inspect(self.engine).get_columns("messages")}:
            with self.engine.begin() as conn:
                if self.engine.dialect.name == "postgresql":
                    conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sender_user_id BIGINT")
                else:
                    conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN sender_user_id BIGINT")

    def checkpoint(self, channel_id: int) -> int:
        with self.engine.connect() as conn:
            row = conn.execute(select(checkpoints.c.message_id).where(checkpoints.c.channel_id == str(channel_id))).scalar_one_or_none()
            return int(row or 0)

    def ingest(self, channel_id: int, rows: list[dict], *, advance_checkpoint: bool = True):
        """Atomically insert messages and advance checkpoint. Reject empty rows."""
        if not rows:
            return 0
        max_id = max(int(r["message_id"]) for r in rows)
        for r in rows:
            if int(r["channel_id"]) != channel_id or not str(r["text"]).strip():
                raise ValueError("invalid incoming Telegram message")
            uid = r.get("sender_user_id")
            if uid is not None and (not isinstance(uid, int) or isinstance(uid, bool)
                                    or not 0 < uid < 2**63):
                raise ValueError("invalid Telegram sender user ID")
        created = now()
        with self.engine.begin() as conn:
            inserted = 0
            for r in rows:
                values = dict(key=f'{channel_id}:{int(r["message_id"])}', channel_id=channel_id,
                    message_id=int(r["message_id"]), channel_name=str(r["channel_name"]),
                    sender_user_id=r.get("sender_user_id"),
                    timestamp=r["timestamp"], text=str(r["text"]),
                    state="pending", tries=0, deep_state="skip", created_at=created)
                inserted += _insert_ignore(conn, messages, values, [messages.c.key])
                # A live event may be stored without a sender, then encountered
                # again in catchup. Fill missing metadata without resetting AI work.
                if values["sender_user_id"] is not None:
                    conn.execute(update(messages).where(and_(
                        messages.c.key == values["key"],
                        messages.c.sender_user_id.is_(None),
                    )).values(sender_user_id=values["sender_user_id"]))
            if advance_checkpoint:
                _insert_ignore(conn, checkpoints, {"channel_id": str(channel_id), "message_id": max_id}, [checkpoints.c.channel_id])
                conn.execute(update(checkpoints).where(and_(checkpoints.c.channel_id == str(channel_id), checkpoints.c.message_id < max_id)).values(message_id=max_id))
            return inserted

    def advance_to(self, channel_id: int, observed_max_id: int) -> None:
        """Advance past observed non-text Telegram service messages, never backwards.

        Call only AFTER all eligible messages through observed_max_id were committed.
        """
        if observed_max_id <= 0:
            return
        with self.engine.begin() as conn:
            _insert_ignore(conn, checkpoints, {"channel_id": str(channel_id), "message_id": observed_max_id}, [checkpoints.c.channel_id])
            conn.execute(update(checkpoints).where(and_(checkpoints.c.channel_id == str(channel_id),
                checkpoints.c.message_id < observed_max_id)).values(message_id=observed_max_id))

    def _claim(self, stage: str, limit: int, max_tries: int = 6):
        at = now()
        field = messages.c.state if stage == "classify" else messages.c.deep_state
        counter = messages.c.tries if stage == "classify" else messages.c.deep_tries
        eligible = or_(and_(field == "pending", or_(messages.c.retry_after.is_(None), messages.c.retry_after <= at)),
            and_(field == "processing", messages.c.lease_until < at))
        token = str(uuid4())
        with self.engine.begin() as conn:
            # A worker can die during its last allowed attempt. Once its lease
            # expires, mark it failed instead of leaving it processing forever.
            conn.execute(update(messages).where(and_(
                field == "processing", messages.c.lease_until < at, counter >= max_tries
            )).values(**{field.key: "failed"}, claim_id=None, lease_until=None))
            q = select(messages).where(and_(eligible, counter < max_tries)).order_by(messages.c.timestamp, messages.c.key).limit(limit).with_for_update(skip_locked=True)
            rows = [dict(row) for row in conn.execute(q).mappings()]
            for row in rows:
                conn.execute(update(messages).where(and_(messages.c.key == row["key"], eligible)).values(
                    **{field.key: "processing"}, claim_id=token, **{counter.key: counter + 1},
                    lease_until=at + timedelta(minutes=15)))
        return rows, token

    def claim_classify(self, limit: int):
        return self._claim("classify", limit)

    def claim_deep(self):
        return self._claim("deep", 1)

    def finish_classify(self, claim_id: str, results: dict, *, deep_enabled: bool = True):
        import json
        with self.engine.begin() as conn:
            for key, result in results.items():
                deep = "pending" if deep_enabled and result.get("priority") == "high" and result.get("relevant") else "skip"
                action = conn.execute(update(messages).where(and_(messages.c.key == key,
                    messages.c.claim_id == claim_id, messages.c.state == "processing")).values(
                        state="done", classification=json.dumps(result, ensure_ascii=False),
                        deep_state=deep, claim_id=None, lease_until=None))
                if action.rowcount != 1:
                    raise RuntimeError("Classification claim lost before commit")

    def finish_deep(self, key: str, claim_id: str, result: dict):
        import json
        with self.engine.begin() as conn:
            action = conn.execute(update(messages).where(and_(messages.c.key == key,
                messages.c.claim_id == claim_id, messages.c.deep_state == "processing")).values(
                    deep_state="done", deep_completed_at=now(), deep_analysis=json.dumps(result, ensure_ascii=False),
                    claim_id=None, lease_until=None))
            if action.rowcount != 1:
                raise RuntimeError("Deep claim lost before commit")

    def retry(self, rows: list[dict], claim_id: str, stage: str):
        field = messages.c.state if stage == "classify" else messages.c.deep_state
        counter_name = "tries" if stage == "classify" else "deep_tries"
        with self.engine.begin() as conn:
            for row in rows:
                next_state = "failed" if row[counter_name] + 1 >= 6 else "pending"
                delay = min(3600, 15 * 2 ** min(row[counter_name], 8))
                conn.execute(update(messages).where(and_(messages.c.key == row["key"], messages.c.claim_id == claim_id,
                    field == "processing")).values(**{field.key: next_state},
                    claim_id=None, lease_until=None, retry_after=now() + timedelta(seconds=delay)))

    def defer(self, rows: list[dict], claim_id: str, stage: str, seconds: int = 3600):
        """Budget/cap exhaustion is not a processing failure; preserve retry count."""
        field = messages.c.state if stage == "classify" else messages.c.deep_state
        counter_name = "tries" if stage == "classify" else "deep_tries"
        with self.engine.begin() as conn:
            for row in rows:
                conn.execute(update(messages).where(and_(messages.c.key == row["key"],
                    messages.c.claim_id == claim_id, field == "processing")).values(
                    **{field.key: "pending", counter_name: max(0, row[counter_name])}, claim_id=None,
                    lease_until=None, retry_after=now() + timedelta(seconds=seconds)))

    def reserve(self, model: str, micro_usd: int, ceiling_usd: int):
        """Reserve worst-case tokens atomically BEFORE issuing a paid request."""
        month = now().strftime("%Y-%m")
        with self.engine.begin() as conn:
            _insert_ignore(conn, budgets, {"month": month, "used_micro_usd": 0}, [budgets.c.month])
            current = conn.execute(select(budgets.c.used_micro_usd).where(budgets.c.month == month).with_for_update()).scalar_one()
            if current + micro_usd > ceiling_usd * 1_000_000:
                return None
            conn.execute(update(budgets).where(budgets.c.month == month).values(used_micro_usd=current + micro_usd))
            rid = str(uuid4())
            conn.execute(insert(requests).values(id=rid, month=month, model=model, state="reserved",
                reserved_micro_usd=micro_usd, at=now()))
        return rid

    def settle(self, request_id: str, charged_micro_usd: int | None, in_tokens: int | None = None, out_tokens: int | None = None):
        """Unknown charge retains the reservation to avoid undercounting ambiguous failures."""
        with self.engine.begin() as conn:
            row = conn.execute(select(requests).where(requests.c.id == request_id).with_for_update()).mappings().one()
            if row["state"] != "reserved":
                raise ValueError("Duplicate settlement")
            actual = max(0, int(charged_micro_usd)) if charged_micro_usd is not None else row["reserved_micro_usd"]
            conn.execute(update(budgets).where(budgets.c.month == row["month"]).values(
                used_micro_usd=budgets.c.used_micro_usd + actual - row["reserved_micro_usd"]))
            conn.execute(update(requests).where(requests.c.id == request_id).values(
                state="confirmed" if charged_micro_usd is not None else "unknown",
                charged_micro_usd=actual, input_tokens=in_tokens, output_tokens=out_tokens))

    def daily_deep_count(self):
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(messages).where(and_(
                messages.c.deep_state == "done", messages.c.deep_completed_at >= now().replace(hour=0, minute=0, second=0, microsecond=0)))).scalar_one()

    def stats(self):
        with self.engine.connect() as conn:
            counts = {state: count for state, count in conn.execute(select(messages.c.state, func.count()).group_by(messages.c.state))}
            deep = {state: count for state, count in conn.execute(select(messages.c.deep_state, func.count()).group_by(messages.c.deep_state))}
            period = now().strftime("%Y-%m")
            spend = conn.execute(select(budgets.c.used_micro_usd).where(budgets.c.month == period)).scalar_one_or_none() or 0
            return {"messages": counts, "deep": deep, "estimated_or_reserved_usd": round(spend / 1_000_000, 6), "month": period}

    def source_progress(self, source_ids):
        """One read-only grouped query for source-level *all-history* analysis.

        No per-OpenAI-request cost can be attributed to a source from the
        existing requests ledger. Callers must not label these lifetime counts
        as the progress of a single recent-N request.
        """
        ids = set(source_ids)
        if len(ids) > 1000 or any(type(pid) is not int or not 0 < pid < 2**63 for pid in ids):
            raise ValueError("Invalid source id list")
        if not ids:
            return {}
        q = (select(messages.c.channel_id, messages.c.state, func.count().label("count"))
             .where(messages.c.channel_id.in_(sorted(ids)))
             .group_by(messages.c.channel_id, messages.c.state))
        totals = {pid: {"total": 0, "done": 0, "pending": 0,
                        "processing": 0, "failed": 0, "other": 0,
                        "percent": 0} for pid in ids}
        with self.engine.connect() as conn:
            for pid, state, count in conn.execute(q):
                row = totals[int(pid)]
                row["total"] += int(count)
                key = state if state in ("done", "pending", "processing", "failed") else "other"
                row[key] += int(count)
        for row in totals.values():
            if row["total"]:
                row["percent"] = round(100 * row["done"] / row["total"], 1)
        return totals

    @staticmethod
    def _safe_object(raw):
        """Malformed or unexpected JSON must not make the whole feed return 500."""
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _event(row):
        dt = row["timestamp"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # SQLite stores UTC without zone metadata.
        return dict(key=row["key"], source=row["channel_name"],
                    sender_user_id=row["sender_user_id"],
                    timestamp=dt.isoformat(),
                    classification=Store._safe_object(row["classification"]),
                    deep_analysis=Store._safe_object(row["deep_analysis"]))

    def events(self, limit: int = 100):
        """Legacy method kept for earlier clients and original project tests."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(messages.c.key, messages.c.channel_name, messages.c.sender_user_id,
                messages.c.timestamp, messages.c.classification, messages.c.deep_analysis)
                .where(messages.c.state == "done")
                .order_by(messages.c.timestamp.desc(), messages.c.key.desc())
                .limit(limit)).mappings().all()
        return [self._event(row) for row in rows]

    def _classification_value(self, field: str):
        """Extract JSON fields, treating invalid historical JSON as NULL."""
        if field not in {"topic", "priority"}:
            raise ValueError("Unsupported classification field")
        if self.engine.dialect.name == "postgresql":
            # pg_input_is_valid(text, type) exists in PostgreSQL 16+, as on
            # this project's PostgreSQL 16-alpine container.
            from sqlalchemy.dialects.postgresql import JSONB
            valid = func.pg_input_is_valid(messages.c.classification, "jsonb")
            parsed = cast(messages.c.classification, JSONB)[field].astext
            return case((valid, parsed), else_=None)
        if self.engine.dialect.name == "sqlite":
            valid = func.json_valid(messages.c.classification)
            parsed = func.json_extract(messages.c.classification, f"$.{field}")
            return case((valid == 1, parsed), else_=None)
        raise RuntimeError("Only PostgreSQL and SQLite are supported")

    @staticmethod
    def _make_cursor(row):
        dt = row["timestamp"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        payload = json.dumps({"t": dt.isoformat(), "k": row["key"]},
                             separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @staticmethod
    def _parse_cursor(token):
        if not token or len(token) > 512 or not token.isascii():
            raise ValueError("Invalid cursor")
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            data = json.loads(raw)
            dt = datetime.fromisoformat(data["t"])
            key = data["k"]
            if not isinstance(key, str) or not 1 <= len(key) <= 100:
                raise ValueError("Invalid key")
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError("Naive cursor date")
            return dt, key
        except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, OverflowError) as exc:
            raise ValueError("Invalid cursor") from exc

    def events_page(self, *, limit: int = 25, offset: int = 0, cursor: str = "",
                    q: str = "", source: str = "", source_id: int | None = None,
                    topic: str = "", priority: str = "",
                    since=None, until=None):
        """Filter *all* processed messages, optionally paginate using a stable keyset.

        Offset pagination remains for existing v8 consumers. The v8.1 UI uses a
        cursor to avoid duplicates as new messages arrive between page fetches.
        """
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000 or (cursor and offset):
            raise ValueError("Invalid pagination parameters")
        terms = [messages.c.state == "done"]
        if q.strip():
            needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{needle}%"
            terms.append(or_(messages.c.text.ilike(pattern, escape="\\"),
                             messages.c.channel_name.ilike(pattern, escape="\\"),
                             messages.c.classification.ilike(pattern, escape="\\")))
        if source:
            terms.append(messages.c.channel_name == source)
        if source_id is not None:
            if type(source_id) is not int or not 0 < source_id < 2**63:
                raise ValueError("Invalid source id")
            terms.append(messages.c.channel_id == source_id)
        if topic:
            terms.append(self._classification_value("topic") == topic)
        if priority:
            terms.append(self._classification_value("priority") == priority)
        if since is not None:
            terms.append(messages.c.timestamp >= since)
        if until is not None:
            terms.append(messages.c.timestamp <= until)
        base_where = and_(*terms)
        if cursor:
            dt, key = self._parse_cursor(cursor)
            terms.append(or_(messages.c.timestamp < dt,
                             and_(messages.c.timestamp == dt, messages.c.key < key)))
        where = and_(*terms)
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(messages).where(base_where)).scalar_one()
            records = conn.execute(select(messages.c.key, messages.c.channel_name, messages.c.sender_user_id,
                messages.c.timestamp, messages.c.classification, messages.c.deep_analysis)
                .where(where).order_by(messages.c.timestamp.desc(), messages.c.key.desc())
                .offset(offset).limit(limit + 1)).mappings().all()
        visible = records[:limit]
        has_more = len(records) > limit
        return {"items": [self._event(row) for row in visible], "total": total,
                "limit": limit, "offset": offset, "has_more": has_more,
                "next_cursor": self._make_cursor(visible[-1]) if has_more and visible else None}

    def filter_options(self):
        """Return sorted source/topic options, ignoring corrupt classifier data."""
        done = messages.c.state == "done"
        topic_expr = self._classification_value("topic")
        with self.engine.connect() as conn:
            sources = conn.execute(select(messages.c.channel_name).where(done)
                                   .distinct().order_by(messages.c.channel_name)).scalars().all()
            topics = conn.execute(select(topic_expr).where(and_(done, messages.c.classification.is_not(None)))
                                  .distinct().order_by(topic_expr)).scalars().all()
        return {"sources": [x for x in sources if x],
                "topics": [x for x in topics if isinstance(x, str) and x]}

    def diagnostics(self):
        """Database-derived indicators, not proof collector or worker are online."""
        at = now()
        with self.engine.connect() as conn:
            ingested = conn.execute(select(func.max(messages.c.created_at))).scalar_one_or_none()
            latest_source = conn.execute(select(func.max(messages.c.timestamp))).scalar_one_or_none()
            pending_oldest = conn.execute(select(func.min(messages.c.created_at))
                .where(messages.c.state == "pending")).scalar_one_or_none()
            stale = conn.execute(select(func.count()).select_from(messages).where(and_(
                messages.c.state == "processing", messages.c.lease_until < at))).scalar_one()
            failed = conn.execute(select(func.count()).select_from(messages)
                .where(messages.c.state == "failed")).scalar_one()
        return {"last_stored_at": ingested.isoformat() if ingested else None,
                "latest_message_time": latest_source.isoformat() if latest_source else None,
                "oldest_pending_stored_at": pending_oldest.isoformat() if pending_oldest else None,
                "expired_processing_leases": stale, "failed_messages": failed,
                "note": "Only database indicators; Docker services are not checked."}
