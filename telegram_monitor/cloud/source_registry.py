"""Persistent, opt-in Telegram source discovery and approval registry.

Groups are the original supported sources. Broadcast news channels can be
included explicitly; users, bots and Saved Messages are never collected.
Discovery itself reads only dialog metadata, not message contents.

Existing groups are baselined on the first successful scan. Broadcast
channels detected after an upgrade require explicit approval by default,
including already subscribed public channels. Auto-enrollment of future
public channels is a separate, disabled-by-default option.
"""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import (MetaData, Table, Column, String, Text, BigInteger,
                        DateTime, Integer, select, update, insert, create_engine, func, and_, or_)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

registry_md = MetaData()
source_audit = Table(
    "tm_source_audit", registry_md,
    Column("id", String(40), primary_key=True),
    Column("peer_id", BigInteger, nullable=False),
    Column("actor", String(50), nullable=False),
    Column("action", String(20), nullable=False),
    Column("from_status", String(20), nullable=False),
    Column("to_status", String(20), nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
)
source_registry = Table(
    "tm_discovered_sources", registry_md,
    Column("peer_id", BigInteger, primary_key=True),
    Column("title", Text, nullable=False),
    Column("username", Text),
    Column("kind", String(30), nullable=False),
    Column("visibility", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)
recent_requests = Table(
    "tm_source_recent_requests", registry_md,
    Column("id", String(40), primary_key=True),
    Column("peer_id", BigInteger, nullable=False),
    Column("message_count", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("scanned_count", Integer),
    Column("saved_count", Integer),
    Column("error_kind", String(40)),
)
RECENT_MAX = 1000

registry_state = Table(
    "tm_source_registry_state", registry_md,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
)

STATUSES = {"baseline", "pending", "approved", "disabled", "left"}


def group_info(dialog):
    """Return metadata for a group dialog, or None for all other chats.

    ``dialog.is_group`` is Telethon's group predicate. Check the raw entity
    too, since broadcast channels must never enter the discovered sources.
    """
    entity = getattr(dialog, "entity", None)
    if entity is None or not getattr(dialog, "is_group", False):
        return None
    if getattr(entity, "migrated_to", None) is not None or getattr(entity, "deactivated", False):
        return None
    if getattr(entity, "megagroup", False):
        kind = "megagroup"
    elif type(entity).__name__ == "Chat":
        kind = "group"
    else:
        return None
    peer_id = getattr(entity, "id", None)
    if type(peer_id) is not int or peer_id <= 0:
        return None
    username = (getattr(entity, "username", None) or "").strip()
    # A legacy Chat is never a public megagroup even if tests provide username.
    is_public = kind == "megagroup" and bool(username)
    title = str(getattr(entity, "title", "") or f"Группа {peer_id}")[:500]
    return {"peer_id": peer_id, "title": title, "username": username or None,
            "kind": kind, "visibility": "public" if is_public else "private"}


def source_info(dialog, *, include_channels=False):
    """Recognize a group or (when enabled) a Telegram broadcast channel.

    ``is_channel`` and ``broadcast`` are both required to avoid confusing
    users, group chats, or megagroups with one-way news channels.
    """
    info = group_info(dialog)
    if info is not None:
        return info
    if not include_channels:
        return None
    entity = getattr(dialog, "entity", None)
    if (entity is None or not getattr(dialog, "is_channel", False) or
            not getattr(entity, "broadcast", False) or
            getattr(entity, "megagroup", False) or
            getattr(entity, "left", False) or
            getattr(entity, "deactivated", False)):
        return None
    peer_id = getattr(entity, "id", None)
    if type(peer_id) is not int or peer_id <= 0:
        return None
    username = (getattr(entity, "username", None) or "").strip()
    title = str(getattr(entity, "title", "") or f"Канал {peer_id}")[:500]
    return {"peer_id": peer_id, "title": title, "username": username or None,
            "kind": "channel", "visibility": "public" if username else "private"}


class SourceRegistry:
    def __init__(self, url, *, initialize=False):
        self.engine = create_engine(url, pool_pre_ping=True, future=True)
        if initialize:
            # Create only the two new registry tables; never alter messages.
            registry_md.create_all(self.engine)

    def _insert_ignore(self, conn, table, values, key):
        if self.engine.dialect.name == "postgresql":
            stmt = pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
        elif self.engine.dialect.name == "sqlite":
            stmt = sqlite_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
        else:
            raise RuntimeError("Unsupported database backend")
        conn.execute(stmt)

    def initialized(self):
        with self.engine.connect() as conn:
            return conn.execute(select(registry_state.c.value).where(
                registry_state.c.key == "baseline_complete")).scalar_one_or_none() == "yes"

    def record_scan(self, groups, *, auto_public=True, auto_public_channels=False):
        """Atomically register one COMPLETE dialog scan.

        Never call on a partial/failed iter_dialogs pass. The first pass
        records existing groups as baseline, without reading messages.
        New broadcast channels are always pending unless separately opted in.
        """
        by_id = {int(g["peer_id"]): g for g in groups}
        if len(by_id) > 10000:
            raise ValueError("Dialog scan too large")
        at = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            initialized = conn.execute(select(registry_state.c.value).where(
                registry_state.c.key == "baseline_complete")).scalar_one_or_none() == "yes"
            existing = {int(r.peer_id): (r.status, r.visibility) for r in conn.execute(
                select(source_registry.c.peer_id, source_registry.c.status,
                       source_registry.c.visibility)).all()}
            # Guard against a seemingly complete but anomalously small snapshot.
            # Example: Telegram unexpectedly returns an empty dialog list. It must
            # not revoke all approvals in one transaction. Retry next scan.
            if initialized and len(existing) >= 10:
                missing = set(existing) - set(by_id)
                if len(missing) > len(existing) // 2:
                    raise ValueError("Suspicious mass disappearance of dialogs; scan ignored")
            for peer_id, item in by_id.items():
                if peer_id in existing:
                    # A departed-and-rejoined group requires manual reapproval.
                    prev_status, prev_visibility = existing[peer_id]
                    next_status = "pending" if prev_status == "left" else prev_status
                    # A group that becomes private must be explicitly approved.
                    if prev_visibility == "public" and item["visibility"] == "private" and next_status == "approved":
                        next_status = "pending"
                    conn.execute(update(source_registry).where(source_registry.c.peer_id == peer_id)
                        .values(title=item["title"], username=item["username"],
                                kind=item["kind"], visibility=item["visibility"],
                                status=next_status, last_seen_at=at))
                else:
                    can_auto_approve = (auto_public_channels if item["kind"] == "channel"
                                        else auto_public)
                    status = "baseline" if not initialized else (
                        "approved" if can_auto_approve and item["visibility"] == "public"
                        else "pending")
                    conn.execute(insert(source_registry).values(
                        **item, status=status, first_seen_at=at, last_seen_at=at))
            if initialized:
                gone = set(existing) - set(by_id)
                for peer_id in gone:
                    # Keep manually disabled groups disabled; no automatic reenrollment.
                    if existing[peer_id][0] != "disabled":
                        conn.execute(update(source_registry)
                            .where(source_registry.c.peer_id == peer_id)
                            .values(status="left"))
            else:
                self._insert_ignore(conn, registry_state,
                                    {"key": "baseline_complete", "value": "yes"},
                                    registry_state.c.key)
        return {"first_scan": not initialized,
                "approved": len(self.approved_ids()), "found": len(by_id)}

    def approved_ids(self):
        with self.engine.connect() as conn:
            return {int(peer_id) for peer_id, in conn.execute(
                select(source_registry.c.peer_id).where(source_registry.c.status == "approved"))}

    def selection_ids(self):
        """Read the current monitoring decisions in one consistent snapshot.

        Explicitly disabled/left sources must not be ingested even when a
        username was configured statically before source discovery existed.
        """
        with self.engine.connect() as conn:
            statuses = conn.execute(
                select(source_registry.c.peer_id, source_registry.c.status)
            ).all()
        approved = {int(peer_id) for peer_id, status in statuses if status == "approved"}
        blocked = {int(peer_id) for peer_id, status in statuses
                   if status in {"disabled", "left"}}
        return approved, blocked


    def list_sources(self, limit=500, *, include_recent=False):
        limit = min(max(int(limit), 1), 1000)
        with self.engine.connect() as conn:
            rows = conn.execute(select(source_registry).order_by(
                source_registry.c.first_seen_at.desc(), source_registry.c.peer_id).limit(limit)).mappings()
            out = []
            for r in rows:
                out.append({"peer_id": int(r["peer_id"]), "title": r["title"],
                            "username": r["username"], "kind": r["kind"],
                            "visibility": r["visibility"], "status": r["status"],
                            "first_seen_at": r["first_seen_at"].isoformat(),
                            "last_seen_at": r["last_seen_at"].isoformat()})
            if include_recent and out:
                peer_ids = {item["peer_id"] for item in out}
                latest = {}
                requests = conn.execute(
                    select(recent_requests).where(recent_requests.c.peer_id.in_(peer_ids))
                    .order_by(recent_requests.c.requested_at.desc(), recent_requests.c.id.desc())
                    .limit(2500)).mappings()
                for request in requests:
                    peer_id = int(request["peer_id"])
                    if peer_id not in latest:
                        latest[peer_id] = {
                            "id": request["id"], "count": request["message_count"],
                            "status": request["status"],
                            "requested_at": request["requested_at"].isoformat(),
                            "finished_at": request["finished_at"].isoformat() if request["finished_at"] else None,
                            "scanned_count": request["scanned_count"],
                            "saved_count": request["saved_count"],
                            "error_kind": request["error_kind"],
                        }
                for item in out:
                    item["recent_request"] = latest.get(item["peer_id"])
            return out

    def recent_audit(self, limit=100):
        limit = min(max(int(limit), 1), 200)
        with self.engine.connect() as conn:
            rows = conn.execute(select(source_audit).order_by(
                source_audit.c.changed_at.desc(), source_audit.c.id.desc()).limit(limit)).mappings()
            return [dict(id=r["id"], peer_id=int(r["peer_id"]), actor=r["actor"],
                         action=r["action"], from_status=r["from_status"],
                         to_status=r["to_status"], changed_at=r["changed_at"].isoformat())
                    for r in rows]

    def change_status(self, peer_id, action, *, actor="dashboard"):
        """Change one status and record a durable audit event atomically."""
        """Dashboard may ONLY approve or disable a discovered group, never edit messages."""
        if type(peer_id) is not int or peer_id <= 0:
            raise ValueError("Invalid group id")
        if action not in {"approve", "disable"}:
            raise ValueError("Unsupported action")
        with self.engine.begin() as conn:
            status = conn.execute(select(source_registry.c.status).where(
                source_registry.c.peer_id == peer_id)).scalar_one_or_none()
            if status is None:
                raise KeyError("Unknown source")
            if status == "left":
                raise ValueError("Source is no longer in the Telegram account")
            target = "approved" if action == "approve" else "disabled"
            if target != status:
                conn.execute(update(source_registry).where(source_registry.c.peer_id == peer_id)
                             .values(status=target))
                conn.execute(insert(source_audit).values(
                    id=str(uuid4()), peer_id=peer_id, actor=actor, action=action,
                    from_status=status, to_status=target,
                    changed_at=datetime.now(timezone.utc)))
            return target

    def request_recent(self, peer_id, count):
        """Queue bounded latest-message ingestion, not a re-classification command.

        Dashboard's dedicated role needs only peer_id/status SELECT on sources,
        and SELECT/INSERT on this request table. No message/checkpoint grants.
        """
        if type(peer_id) is not int or peer_id <= 0 or type(count) is not int or not 1 <= count <= RECENT_MAX:
            raise ValueError("Invalid recent-message request")
        with self.engine.begin() as conn:
            source = select(source_registry.c.status).where(source_registry.c.peer_id == peer_id)
            if self.engine.dialect.name == "postgresql":
                source = source.with_for_update()
            status = conn.execute(source).scalar_one_or_none()
            if status is None:
                raise KeyError("Unknown source")
            if status != "approved":
                raise ValueError("Enable monitoring before requesting recent messages")
            active = conn.execute(select(recent_requests.c.id).where(and_(
                recent_requests.c.peer_id == peer_id,
                recent_requests.c.status.in_(("pending", "running")))).limit(1)).first()
            if active:
                raise ValueError("A request for this group is already active")
            # Global guard to avoid flooding Telegram and OpenAI across groups.
            backlog = conn.execute(select(func.count()).select_from(recent_requests)
                .where(recent_requests.c.status.in_(("pending", "running")))).scalar_one()
            if backlog >= 10:
                raise ValueError("Too many pending group requests")
            request_id = str(uuid4())
            conn.execute(insert(recent_requests).values(
                id=request_id, peer_id=peer_id, message_count=count,
                status="pending", requested_at=datetime.now(timezone.utc)))
        return {"id": request_id, "count": count, "status": "pending"}

    def claim_recent(self, active_peer_ids):
        """Collector atomically claims one request only for active sources.

        Stale running requests are retried; Store.ingest deduplicates messages.
        """
        ids = sorted({int(pid) for pid in active_peer_ids if int(pid) > 0})
        if not ids:
            return None
        now = datetime.now(timezone.utc)
        stale = now - timedelta(minutes=20)
        with self.engine.begin() as conn:
            query = (select(recent_requests.c.id, recent_requests.c.peer_id,
                            recent_requests.c.message_count)
                .where(and_(
                    recent_requests.c.peer_id.in_(ids),
                    or_(recent_requests.c.status == "pending",
                        and_(recent_requests.c.status == "running",
                             recent_requests.c.started_at < stale))))
                .order_by(recent_requests.c.requested_at, recent_requests.c.id).limit(1))
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            row = conn.execute(query).first()
            if row is None:
                return None
            conn.execute(update(recent_requests).where(recent_requests.c.id == row.id)
                .values(status="running", started_at=now, error_kind=None))
            return {"id": row.id, "peer_id": int(row.peer_id), "count": row.message_count}

    def finish_recent(self, request_id, *, scanned=0, saved=0, error=None):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 40:
            raise ValueError("Invalid request id")
        if not 0 <= scanned <= RECENT_MAX or not 0 <= saved <= scanned:
            raise ValueError("Invalid recent-message counters")
        if error is not None and (not isinstance(error, str) or len(error) > 40):
            raise ValueError("Invalid safe error kind")
        with self.engine.begin() as conn:
            result = conn.execute(update(recent_requests).where(and_(
                recent_requests.c.id == request_id, recent_requests.c.status == "running"))
                .values(status="failed" if error else "done",
                        finished_at=datetime.now(timezone.utc),
                        scanned_count=scanned, saved_count=saved, error_kind=error))
        return result.rowcount == 1

    def dispose(self):
        self.engine.dispose()
