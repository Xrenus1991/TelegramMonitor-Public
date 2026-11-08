-- Raise existing PostgreSQL per-source recent history request cap from 200 to 1000.
-- Data-only safe: replaces CHECK constraints; does not update, delete or reset messages.
-- Requires an existing tm_source_recent_requests table and a verified backup.
BEGIN;
SET LOCAL lock_timeout = '8s';
SET LOCAL statement_timeout = '45s';
DO $migration$
DECLARE
    old_count text;
    old_scanned text;
    old_saved text;
BEGIN
    IF to_regclass('public.tm_source_recent_requests') IS NULL THEN
        RAISE EXCEPTION 'tm_source_recent_requests is missing';
    END IF;
    -- Refuse unexpected legacy definitions rather than silently removing guards.
    SELECT pg_get_constraintdef(oid) INTO old_count FROM pg_constraint
    WHERE conrelid = 'public.tm_source_recent_requests'::regclass
      AND conname = 'tm_source_recent_requests_message_count_check';
    SELECT pg_get_constraintdef(oid) INTO old_scanned FROM pg_constraint
    WHERE conrelid = 'public.tm_source_recent_requests'::regclass
      AND conname = 'tm_source_recent_requests_scanned_count_check';
    SELECT pg_get_constraintdef(oid) INTO old_saved FROM pg_constraint
    WHERE conrelid = 'public.tm_source_recent_requests'::regclass
      AND conname = 'tm_source_recent_requests_saved_count_check';
    IF old_count IS NOT NULL AND (
        position('200' IN old_count)=0 OR position('message_count' IN old_count)=0)
    THEN RAISE EXCEPTION 'Unexpected existing message_count constraint'; END IF;
    IF old_scanned IS NOT NULL AND (
        position('200' IN old_scanned)=0 OR position('scanned_count' IN old_scanned)=0)
    THEN RAISE EXCEPTION 'Unexpected existing scanned_count constraint'; END IF;
    IF old_saved IS NOT NULL AND (
        position('200' IN old_saved)=0 OR position('saved_count' IN old_saved)=0)
    THEN RAISE EXCEPTION 'Unexpected existing saved_count constraint'; END IF;
    ALTER TABLE public.tm_source_recent_requests
        DROP CONSTRAINT IF EXISTS tm_source_recent_requests_message_count_check,
        DROP CONSTRAINT IF EXISTS tm_source_recent_requests_scanned_count_check,
        DROP CONSTRAINT IF EXISTS tm_source_recent_requests_saved_count_check;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.tm_source_recent_requests'::regclass
        AND conname='tm_source_recent_message_count_1000_check') THEN
        ALTER TABLE public.tm_source_recent_requests
            ADD CONSTRAINT tm_source_recent_message_count_1000_check
                CHECK (message_count BETWEEN 1 AND 1000);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.tm_source_recent_requests'::regclass
        AND conname='tm_source_recent_scanned_count_1000_check') THEN
        ALTER TABLE public.tm_source_recent_requests
            ADD CONSTRAINT tm_source_recent_scanned_count_1000_check
                CHECK (scanned_count BETWEEN 0 AND 1000);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.tm_source_recent_requests'::regclass
        AND conname='tm_source_recent_saved_count_1000_check') THEN
        ALTER TABLE public.tm_source_recent_requests
            ADD CONSTRAINT tm_source_recent_saved_count_1000_check
                CHECK (saved_count BETWEEN 0 AND 1000);
    END IF;
END $migration$;
COMMIT;
