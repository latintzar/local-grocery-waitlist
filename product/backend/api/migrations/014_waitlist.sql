-- 014_waitlist.sql · Vitrina engagement-ranked waitlist
--
-- See the build-vs-buy decision at
-- apps/vitrina/company/decisions/2026-05-06-waitlist-backend-build-vs-buy.md
-- for why this lives in our own table rather than a SaaS.
--
-- Engagement score (computed server-side via vitrina_waitlist_score):
--   invites = +25 each
--   call    = +15  (set by the founder dashboard's "Did call?" checkbox)
--   quiz    = +5   (set when all five sign-up questions are answered)
--
-- Position is rank by (score desc, created_at asc) within a city.

create extension if not exists "pgcrypto";

create table if not exists vitrina_waitlist (
  id                  uuid primary key default gen_random_uuid(),
  city                text not null default 'Sofia',

  -- contact ----------------------------------------------------
  phone               text not null,
  email               text,
  first_name          text,

  -- home address ----------------------------------------------
  home_address        text,
  home_neighbourhood  text,
  home_lat            double precision,
  home_lng            double precision,

  -- optional work address -------------------------------------
  work_company        text,
  work_address        text,
  work_neighbourhood  text,
  work_lat            double precision,
  work_lng            double precision,

  -- referral graph --------------------------------------------
  referral_code       text,
  referred_by         text,
  invited_count       int  not null default 0,

  -- engagement signals ---------------------------------------
  did_call            bool not null default false,
  did_quiz            bool not null default false,
  quiz_answers        jsonb,

  -- bookkeeping ----------------------------------------------
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- one signup per phone per city
create unique index if not exists vitrina_waitlist_phone_city_idx
  on vitrina_waitlist(city, phone);

-- referral_code is globally unique across cities so vitrina.bg/?r=XYZ
-- maps to one row anywhere
create unique index if not exists vitrina_waitlist_referral_code_idx
  on vitrina_waitlist(referral_code)
  where referral_code is not null;

-- helpful sort indexes
create index if not exists vitrina_waitlist_city_created_idx
  on vitrina_waitlist(city, created_at);

create index if not exists vitrina_waitlist_referred_by_idx
  on vitrina_waitlist(referred_by)
  where referred_by is not null;


-- ── score function ─────────────────────────────────────────────
-- Single source of truth for the engagement score. Used by the
-- /api/admin/waitlist/list endpoint and by the position re-rank.

-- NB: parameter named `r` (not `row`) — `row` is a reserved keyword in PG.
create or replace function vitrina_waitlist_score(r vitrina_waitlist)
returns int language sql immutable as $$
  select (coalesce(r.invited_count, 0) * 25)
       + (case when r.did_call then 15 else 0 end)
       + (case when r.did_quiz then  5 else 0 end);
$$;


-- ── updated_at trigger ─────────────────────────────────────────

create or replace function vitrina_waitlist_touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists vitrina_waitlist_touch on vitrina_waitlist;
create trigger vitrina_waitlist_touch
  before update on vitrina_waitlist
  for each row execute function vitrina_waitlist_touch_updated_at();
