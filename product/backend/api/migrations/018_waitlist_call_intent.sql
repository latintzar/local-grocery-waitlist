-- 018_waitlist_call_intent.sql · Vitrina waitlist
--
-- Persist the "user wants a founder call" signal that today only fires
-- a Telegram/WhatsApp ping to Peter via /api/waitlist/track-call-intent
-- without leaving a trace on the row. Founder dashboard needs to see
-- WHO has requested a call so we can return them — not just "someone
-- did, somewhere".
--
-- Two columns:
--   * ``call_intent_at`` — last time this person tapped the "talk to a
--     founder" CTA on the position page. Single timestamp, overwritten
--     on each tap (we don't need a history; the dashboard just needs
--     "tap me back").
--   * ``call_intent_channel`` — which chat target they picked
--     ('whatsapp' | 'viber' | other). Useful so the founder dashboard
--     can highlight the right contact button.
--
-- Idempotent: re-running the file is safe.

alter table vitrina_waitlist
  add column if not exists call_intent_at      timestamptz,
  add column if not exists call_intent_channel text;

create index if not exists vitrina_waitlist_call_intent_idx
  on vitrina_waitlist(call_intent_at)
  where call_intent_at is not null;
