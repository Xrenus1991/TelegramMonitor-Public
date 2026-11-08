"""Read-only cloud dashboard: bind localhost and access via SSH tunnel only."""
import hmac
from functools import lru_cache
from pathlib import Path
import logging
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date, timedelta, timezone
import os
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .db import Store
from .analytics import fetch_processed, duplicate_summary, daily_brief, utc_day_bounds, signal_summary
from .source_registry import SourceRegistry
from .comments import CommentStore
from .sherlock_proxy import router as sherlock_router
from pydantic import BaseModel, StrictInt, Field
from typing import Literal

app = FastAPI(title="Telegram Monitor v8.6.1 (comment review; source decisions audited)", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def privacy_headers(request, call_next):
    # Even browser/API proxy caches must not retain sensitive event summaries.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response
security = HTTPBasic()


def authorize(credentials: HTTPBasicCredentials = Depends(security)):
    user = os.environ.get("DASHBOARD_USER", "admin")
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password or len(password) < 16 or not (hmac.compare_digest(credentials.username.encode("utf-8"), user.encode("utf-8")) and
            hmac.compare_digest(credentials.password.encode("utf-8"), password.encode("utf-8"))):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"})
    return True


@lru_cache(maxsize=1)
def store():
    return Store(os.environ.get("DATABASE_URL") or _db_url(), read_only=True)


def _db_url():
    """Use separately provisioned limited DB credentials when configured.

    Without DASHBOARD_DB_USER/PASSWORD the old credentials still work for
    backwards compatibility; see sql/dashboard_role_template.sql for hardening.
    """
    from sqlalchemy.engine import URL
    limited_user = os.getenv("DASHBOARD_DB_USER", "").strip()
    limited_password = os.getenv("DASHBOARD_DB_PASSWORD", "")
    if bool(limited_user) != bool(limited_password):
        raise RuntimeError("Both DASHBOARD_DB_USER and DASHBOARD_DB_PASSWORD are required")
    return URL.create("postgresql+psycopg",
        username=limited_user or os.getenv("DB_USER", "monitor"),
        password=limited_password if limited_user else os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "postgres"),
        database=os.getenv("DB_NAME", "monitor")).render_as_string(hide_password=False)


@app.get("/health")
def health():
    # Does not reveal whether Telegram or OpenAI are healthy, only HTTP liveness.
    return {"service": "up"}


@app.get("/api/stats", dependencies=[Depends(authorize)])
def stats():
    return _db_call("stats")


@app.get("/api/events", dependencies=[Depends(authorize)])
def events(limit: int = Query(25, ge=1, le=100),
           offset: int = Query(0, ge=0, le=100000),
           cursor: str = Query("", max_length=512),
           q: str = Query("", max_length=200),
           source: str = Query("", max_length=300),
           source_id: int | None = Query(None, gt=0, lt=9223372036854775808),
           topic: str = Query("", max_length=200),
           priority: str = Query("", max_length=100),
           since: datetime | None = None,
           until: datetime | None = None):
    if (since and since.utcoffset() is None) or (until and until.utcoffset() is None):
        raise HTTPException(422, detail="Dates must include a timezone offset")
    if since and until and since > until:
        raise HTTPException(422, detail="since must not exceed until")
    if cursor and offset:
        raise HTTPException(422, detail="Do not combine cursor and offset")
    return _db_call("events_page", limit=limit, offset=offset, cursor=cursor,
                    q=q, source=source, source_id=source_id, topic=topic, priority=priority,
                    since=since, until=until)


@app.get("/api/filters", dependencies=[Depends(authorize)])
def filters():
    return _db_call("filter_options")


@app.get("/api/diagnostics", dependencies=[Depends(authorize)])
def diagnostics():
    return _db_call("diagnostics")


@app.get("/api/analytics/duplicates", dependencies=[Depends(authorize)])
def analytics_duplicates(hours: int = Query(72)):
    """Conservative TEXT repetition analysis, without OpenAI or writes."""
    # Query strings arrive as text; Literal[int] rejects valid values like "24".
    if hours not in (24, 72, 168):
        raise HTTPException(422, detail="hours must be 24, 72 or 168")
    end = datetime.now(timezone.utc)
    try:
        rows, truncated = fetch_processed(store().engine, end - timedelta(hours=hours), end, cap=1000)
        return duplicate_summary(rows, truncated, hours=hours)
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Read-only analytics unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Analytics temporarily unavailable") from exc


@app.get("/api/analytics/daily", dependencies=[Depends(authorize)])
def analytics_daily(day: date | None = None):
    """On-demand factual counts and selection of previously classified posts, UTC."""
    day = day or datetime.now(timezone.utc).date()
    if day > datetime.now(timezone.utc).date():
        raise HTTPException(422, detail="Future daily report is unavailable")
    begin, end = utc_day_bounds(day)
    try:
        rows, truncated = fetch_processed(store().engine, begin, end, cap=2000)
        result = daily_brief(rows, truncated, day=day)
        # Extend the existing daily digest without modifying earlier message rows.
        # Discussion authors exist only for opt-in v8.6.1 comment ingestion.
        if _discovery_flag('CLOUD_COMMENTS_ENABLED'):
            try:
                result['possible_direct_threats'] = {'enabled': True, **comment_reader().alerts_between(begin, end, limit=30)}
            except SQLAlchemyError as err:
                logging.getLogger(__name__).warning('Comment digest unavailable (%s)', type(err).__name__)
                result['possible_direct_threats'] = {'enabled': True, 'unavailable': True, 'items': []}
        else:
            result['possible_direct_threats'] = {'enabled': False, 'items': [], 'count': 0}
        return result
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Read-only daily report unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Daily report temporarily unavailable") from exc



@app.get("/api/analytics/signals", dependencies=[Depends(authorize)])
def analytics_signals(hours: int = Query(72),
                      category: str = Query("candidate", max_length=20),
                      q: str = Query("", max_length=160)):
    """Read-only triage/search; no changes to the Collector or Worker."""
    if hours not in (24, 72, 168):
        raise HTTPException(422, detail="hours must be 24, 72 or 168")
    if category not in {"candidate", "likely_ad", "other", "all"}:
        raise HTTPException(422, detail="Unsupported category")
    if q.strip() and len(q.strip()) < 2:
        raise HTTPException(422, detail="Search must be at least two characters")
    end = datetime.now(timezone.utc)
    try:
        rows, truncated = fetch_processed(store().engine, end - timedelta(hours=hours), end, cap=1000)
        return signal_summary(rows, truncated, hours=hours, category=category, query=q.strip())
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Signal triage unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Signal triage temporarily unavailable") from exc



@lru_cache(maxsize=1)
def comment_reader():
    # No schema creation, read-only connection. Collector owns the two tables.
    return CommentStore(os.environ.get("DATABASE_URL") or _db_url(), read_only=True)


@app.get("/api/comments/overview", dependencies=[Depends(authorize)])
def comments_overview(hours: int = Query(72)):
    if hours not in (24, 72, 168):
        raise HTTPException(422, detail="hours must be 24, 72 or 168")
    if not _discovery_flag("CLOUD_COMMENTS_ENABLED"):
        return {"enabled": False, "initialized": False, "threads": [], "total_comments": 0}
    try:
        return {"enabled": True, **comment_reader().overview(hours=hours, limit=30)}
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Comment overview unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Comment overview temporarily unavailable") from exc


@app.get("/api/comments/thread", dependencies=[Depends(authorize)])
def comments_thread(channel_id: int = Query(..., gt=0), post_id: int = Query(..., gt=0)):
    if not _discovery_flag("CLOUD_COMMENTS_ENABLED"):
        return {"enabled": False, "items": [], "count": 0}
    try:
        return {"enabled": True, **comment_reader().thread(channel_id, post_id, limit=50)}
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Comment thread unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Comment thread temporarily unavailable") from exc


@app.get("/api/comments/alerts", dependencies=[Depends(authorize)])
def comments_alerts(hours: int = Query(72)):
    if hours not in (24, 72, 168):
        raise HTTPException(422, detail="hours must be 24, 72 or 168")
    if not _discovery_flag('CLOUD_COMMENTS_ENABLED'):
        return {'enabled': False, 'count': 0, 'items': []}
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours)
    try:
        return {'enabled': True, **comment_reader().alerts_between(begin, end, limit=30)}
    except SQLAlchemyError as err:
        logging.getLogger(__name__).error('Comment alert digest unavailable (%s)', type(err).__name__)
        raise HTTPException(503, detail='Comment alerts temporarily unavailable') from err


def _discovery_flag(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def sources_registry():
    # Dashboard browsing remains read-only, using its existing restricted role.
    return SourceRegistry(os.environ.get("DATABASE_URL") or _db_url(), initialize=False)


def _source_writes_enabled():
    # A separate, narrowly granted PostgreSQL role is REQUIRED for decisions.
    # Never reuse the analytics credentials or silently elevate DB permissions.
    return (_discovery_flag("CLOUD_DISCOVERY_ENABLED") and
            _discovery_flag("CLOUD_DISCOVERY_UI_WRITE") and
            bool(os.environ.get("CLOUD_SOURCE_CONTROL_DATABASE_URL", "").strip()))


@lru_cache(maxsize=1)
def source_decision_registry():
    url = os.environ.get("CLOUD_SOURCE_CONTROL_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Source control credentials are not configured")
    return SourceRegistry(url, initialize=False)


@app.get("/api/sources/audit", dependencies=[Depends(authorize)])
def source_decision_history(limit: int = Query(50, ge=1, le=200)):
    if not _discovery_flag("CLOUD_DISCOVERY_ENABLED", False):
        return {"items": []}
    try:
        return {"items": sources_registry().recent_audit(limit)}
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Source audit unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Source registry temporarily unavailable") from exc


@app.get("/api/sources", dependencies=[Depends(authorize)])
def discovered_sources():
    enabled = _discovery_flag("CLOUD_DISCOVERY_ENABLED", False)
    writable = _source_writes_enabled()
    recent_enabled = writable and _discovery_flag("CLOUD_SOURCE_RECENT_ENABLED")
    if not enabled:
        return {"enabled": False, "editable": False, "recent_enabled": False, "initialized": False, "items": []}
    try:
        registry = sources_registry()
        items = registry.list_sources(include_recent=recent_enabled)
        progress_available = True
        try:
            progress = store().source_progress({row["peer_id"] for row in items})
        except SQLAlchemyError as exc:
            # Source management must remain usable when analytics is unavailable.
            logging.getLogger(__name__).error("Source progress unavailable (%s)", type(exc).__name__)
            progress = {}
            progress_available = False
        for item in items:
            item["analysis_progress"] = progress.get(item["peer_id"])
        return {"enabled": True, "editable": writable, "recent_enabled": recent_enabled,
                "progress_available": progress_available,
                "initialized": registry.initialized(), "items": items}
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Source list unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Source registry temporarily unavailable") from exc


class SourceDecision(BaseModel):
    action: Literal["approve", "disable"]


class RecentMessagesRequest(BaseModel):
    count: StrictInt = Field(..., ge=1, le=1000)


def _validate_source_action(request: Request):
    """Require authenticated same-origin JSON (a direct HTTP Basic write is CSRF-sensitive)."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    if (not host or origin != "http://" + host or
            request.headers.get("x-tm-action") != "1" or
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
        raise HTTPException(403, detail="Same-origin JSON request required")


@app.post("/api/sources/{peer_id}", dependencies=[Depends(authorize)])
def decide_source(peer_id: int, decision: SourceDecision, request: Request):
    if not _source_writes_enabled():
        raise HTTPException(403, detail="Source management disabled")
    _validate_source_action(request)
    try:
        result = source_decision_registry().change_status(peer_id, decision.action)
        return {"peer_id": peer_id, "status": result}
    except KeyError as exc:
        raise HTTPException(404, detail="Unknown source") from exc
    except ValueError as exc:
        raise HTTPException(409, detail="Source not available for this action") from exc
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Source update failed (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Source registry temporarily unavailable") from exc


@app.post("/api/sources/{peer_id}/recent", status_code=202, dependencies=[Depends(authorize)])
def request_source_recent(peer_id: int, body: RecentMessagesRequest, request: Request):
    """Queue an opt-in historical fetch; processed rows are not reclassified."""
    if not _source_writes_enabled() or not _discovery_flag("CLOUD_SOURCE_RECENT_ENABLED"):
        raise HTTPException(403, detail="Recent message requests disabled")
    _validate_source_action(request)
    try:
        result = source_decision_registry().request_recent(peer_id, body.count)
        return {"peer_id": peer_id, **result, "note": "New messages enter the existing processing queue; already processed messages are not reclassified"}
    except KeyError as exc:
        raise HTTPException(404, detail="Unknown source") from exc
    except ValueError as exc:
        raise HTTPException(409, detail="Source not approved or another request is active") from exc
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Recent request unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Recent request storage temporarily unavailable") from exc


def _db_call(method, **kwargs):
    """Hide DB internals from clients, while preserving HTTP 422 validation."""
    try:
        return getattr(store(), method)(**kwargs)
    except ValueError as exc:
        # Cursor decoding errors are user input problems, not internal errors.
        raise HTTPException(422, detail="Invalid filter or pagination parameter") from exc
    except SQLAlchemyError as exc:
        # Don't emit SQL parameters, database credentials or channel names to logs.
        logging.getLogger(__name__).error("Dashboard database operation failed: %s", type(exc).__name__)
        raise HTTPException(503, detail="Database temporarily unavailable") from exc


# Manual Sherlock OSINT; inherits existing Dashboard HTTP Basic authorization.
app.include_router(sherlock_router, dependencies=[Depends(authorize)])
from .translation_proxy import router as translation_router
app.include_router(translation_router, dependencies=[Depends(authorize)])

# Kept outside the Python source for readable, independently testable UI markup.
PAGE = Path(__file__).with_name("dashboard_v8_3.html").read_text(encoding="utf-8")
SHERLOCK_PAGE = Path(__file__).with_name("sherlock_full.html").read_text(encoding="utf-8")


@app.get("/sherlock", response_class=HTMLResponse, dependencies=[Depends(authorize)])
def sherlock_console():
    return HTMLResponse(SHERLOCK_PAGE, headers={"Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(authorize)])
def dashboard():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@app.get("/api/text/variants", dependencies=[Depends(authorize)])
def text_variants(key: str = Query(..., min_length=3, max_length=100, pattern=r"^[0-9]+:[0-9]+$")):
    """On-demand, bounded derived text; never writes originals or classifications."""
    if not _discovery_flag("CLOUD_TEXT_VARIANTS_ENABLED"):
        raise HTTPException(503, detail="Text variants are disabled")
    from sqlalchemy import select, func
    from .db import messages
    from .unicode_context import inspect_unicode, MAX_CHARS
    try:
        with store().engine.connect() as connection:
            row = connection.execute(select(
                func.substr(messages.c.text, 1, MAX_CHARS + 1),
                func.length(messages.c.text),
            ).where(messages.c.key == key)).first()
    except SQLAlchemyError as exc:
        logging.getLogger(__name__).error("Text variants unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail="Text variants temporarily unavailable") from exc
    if row is None:
        raise HTTPException(404, detail="Message not found")
    report = inspect_unicode(row[0], enabled=True)
    if not report["available"]:
        raise HTTPException(503, detail="Text variant processor unavailable")
    original = row[0][:MAX_CHARS]
    derived = original
    findings = [finding for finding in report["findings"] if finding["end"] <= MAX_CHARS]
    for finding in reversed(findings):
        derived = derived[:finding["start"]] + finding["suggestion"] + derived[finding["end"]:]
    return {"key": key, "version": report["version"], "original_excerpt": original,
            "normalized_excerpt": derived, "source_length": row[1],
            "truncated": row[1] > MAX_CHARS, "findings": findings,
            "persisted": False, "assessment": "Text variant; not evidence of intent or threat"}


@app.get("/api/text/search-variants", dependencies=[Depends(authorize)])
def search_text_variants(q: str = Query(..., min_length=2, max_length=200),
                         source_id: int | None = Query(None, gt=0, lt=9223372036854775808),
                         before: datetime | None = None,
                         before_key: str = Query("", max_length=100, pattern=r"^(?:[0-9]+:[0-9]+)?$")):
    """Search a bounded recent window; return keys, never imply full-history coverage."""
    from sqlalchemy import select, func, and_, or_
    from .db import messages
    from .variant_search import search_variants, MAX_CANDIDATES
    if not _discovery_flag("CLOUD_VARIANT_SEARCH_ENABLED"):
        raise HTTPException(503, detail="Variant search disabled")
    if len(q.strip()) < 2:
        raise HTTPException(422, detail="Search must contain at least two characters")
    if before and before.utcoffset() is None:
        raise HTTPException(422, detail="Dates must include a timezone offset")
    if before_key and before is None:
        raise HTTPException(422, detail="before_key requires before timestamp")
    statement = select(messages.c.key, messages.c.timestamp,
                       func.substr(messages.c.text, 1, 4001).label("text"))
    if source_id is not None:
        statement = statement.where(messages.c.channel_id == source_id)
    if before is not None:
        if before_key:
            statement = statement.where(or_(messages.c.timestamp < before,
                and_(messages.c.timestamp == before, messages.c.key < before_key)))
        else:
            statement = statement.where(messages.c.timestamp < before)
    statement = statement.order_by(messages.c.timestamp.desc(), messages.c.key.desc()).limit(MAX_CANDIDATES + 1)
    try:
        with store().engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        result = search_variants(rows, q, enabled=True)
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(503, detail="Variant search temporarily unavailable")
    result["next_before"] = None
    result["next_before_key"] = None
    if len(rows) > MAX_CANDIDATES:
        boundary = rows[MAX_CANDIDATES - 1]
        stamp = boundary["timestamp"]
        if stamp.utcoffset() is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        result["next_before"] = stamp.isoformat()
        result["next_before_key"] = boundary["key"]
    result["scope"] = "latest_candidate_window"
    result["source_id"] = source_id
    return result
