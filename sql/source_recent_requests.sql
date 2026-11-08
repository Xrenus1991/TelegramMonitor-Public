-- TelegramMonitor: additive schema for per-source "analyze latest N" requests.
-- Run with psql -v ON_ERROR_STOP=1 -v reader_role=... -v controller_role=...
--               -v collector_role=... -f sql/source_recent_requests.sql
-- PostgreSQL identifiers are quoted by psql. Never store passwords in SQL files.
-- Take a verified database + roles backup before applying in production.
BEGIN;
CREATE TABLE IF NOT EXISTS public.tm_source_recent_requests (
    id varchar(40) PRIMARY KEY,
    peer_id bigint NOT NULL REFERENCES public.tm_discovered_sources(peer_id),
    message_count integer NOT NULL CHECK (message_count BETWEEN 1 AND 1000),
    status varchar(20) NOT NULL CHECK (status IN ('pending','running','done','failed')),
    requested_at timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    scanned_count integer CHECK (scanned_count BETWEEN 0 AND 1000),
    saved_count integer CHECK (saved_count BETWEEN 0 AND 1000),
    error_kind varchar(40),
    CONSTRAINT source_recent_saved_not_greater_than_scanned
        CHECK (saved_count IS NULL OR scanned_count IS NULL OR saved_count <= scanned_count)
);
CREATE INDEX IF NOT EXISTS tm_recent_requests_status_time_idx
    ON public.tm_source_recent_requests(status, requested_at);
CREATE INDEX IF NOT EXISTS tm_recent_requests_source_time_idx
    ON public.tm_source_recent_requests(peer_id, requested_at DESC);
-- Existing Dashboard credentials remain read-only.
GRANT SELECT ON public.tm_source_recent_requests TO :"reader_role";
-- The HTTP write role cannot read or update Telegram messages.
GRANT SELECT (id, peer_id, status) ON public.tm_source_recent_requests TO :"controller_role";
GRANT INSERT (id, peer_id, message_count, status, requested_at)
    ON public.tm_source_recent_requests TO :"controller_role";
-- Collector owns request lifecycle and message ingestion. No DELETE permission.
GRANT SELECT, INSERT, UPDATE ON public.tm_source_recent_requests TO :"collector_role";
COMMIT;
