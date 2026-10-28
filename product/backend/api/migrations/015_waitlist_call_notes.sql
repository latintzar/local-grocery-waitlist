-- 015_waitlist_call_notes.sql — applied 2026-05-06 via Supabase MCP.
--
-- The founder dashboard's "Log call" modal posts to
-- ``POST /api/admin/waitlist/log-call`` with a Markdown notes blob
-- (free text + optional speaker-diarized transcript). Both columns
-- below back that flow.
--
-- - ``call_notes`` is rendered verbatim in the modal next time the row
--   is opened, so the founder can keep adding context call after call.
-- - ``call_logged_at`` records the latest save so the dashboard can
--   show "Logged 5 hours ago" without needing a separate audit table.
--
-- The +15 congratulations email fires once on the 0→1 ``did_call``
-- transition (see ``_apply_call_log`` in ``waitlist.py``). Subsequent
-- saves only update the notes — they don't re-spam the caller.

ALTER TABLE vitrina_waitlist
  ADD COLUMN IF NOT EXISTS call_notes TEXT,
  ADD COLUMN IF NOT EXISTS call_logged_at TIMESTAMPTZ;

COMMENT ON COLUMN vitrina_waitlist.call_notes IS
  'Markdown notes from the founder dashboard call-log modal. Combines free-text notes with optional speaker-diarized transcript.';
COMMENT ON COLUMN vitrina_waitlist.call_logged_at IS
  'When the founder pressed Save in the call-log modal. NULL means never logged.';
