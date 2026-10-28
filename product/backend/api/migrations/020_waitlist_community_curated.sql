-- 020_waitlist_community_curated.sql · Vitrina waitlist community-options curator
--
-- Why this exists
-- ---------------
-- Three quiz questions (`buy`, `ripped`, `wish`) accept free-text
-- community submissions on the waitlist. Today the read endpoint
-- (`GET /api/waitlist/community_answers`) just casefold-aggregates the
-- raw `quiz_answers` JSONB column and returns the result language-
-- agnostic, so a Bulgarian visitor sees English chips and vice versa,
-- typos and near-duplicates aren't merged, and offensive entries are
-- only filtered client-side against preset matches.
--
-- This table holds an LLM-curated view of the community pool, refreshed
-- in the background after every quiz submit. One row per question id;
-- the curator overwrites the row in place, so this table never grows
-- past `len(_COMMUNITY_QIDS)` rows total. Dropped items (offensive,
-- nonsense, off-topic) are stored alongside for spot-check / audit.
--
-- The frontend asks for `?lang=bg|en` and receives the matching
-- localised array. If no curated row exists yet (cold start, or LLM
-- outage), the read endpoint falls back to today's raw aggregation —
-- so the UX never breaks because of a curator failure.

create table if not exists vitrina_waitlist_community_curated (
  qid                            text primary key,
  -- Curated arrays — each item is { label, count, merged_from: [...] }.
  -- `count` is the SUM of merged inputs' counts, so the frontend can
  -- still rank by community popularity.
  items_en                       jsonb not null default '[]'::jsonb,
  items_bg                       jsonb not null default '[]'::jsonb,
  -- Items the curator dropped, kept for audit. Each: { label, reason }.
  dropped                        jsonb not null default '[]'::jsonb,
  -- Provenance / cache-busting metadata.
  last_curated_at                timestamptz not null default now(),
  submission_count_at_curation   integer not null default 0,
  model_used                     text,
  prompt_version                 integer not null default 1,
  -- Whether the last curation succeeded. False means the read endpoint
  -- should still prefer the previous good snapshot (which is in this
  -- row from the prior successful run; the curator never overwrites
  -- the items_* arrays on failure).
  last_run_ok                    boolean not null default true,
  last_error                     text
);

-- Defence in depth: only `service_role` writes (the API uses that key);
-- nothing else needs access. Reads happen through the API layer too,
-- so anon/authenticated roles get no direct access.
alter table vitrina_waitlist_community_curated enable row level security;

-- (No policies created — service_role bypasses RLS by default in
-- Supabase. Adding `to anon` policies later is how this would become
-- directly readable by the browser.)
