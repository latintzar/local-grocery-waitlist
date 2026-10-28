-- 017_geocode_usage.sql · Vitrina Mapbox geocoding rate cap
--
-- Why this exists
-- ---------------
-- The waitlist address picker (apps/vitrina/product/web/public/waitlist/)
-- now talks to Mapbox Search via a server-side proxy at /api/geocode/*
-- instead of going to Nominatim direct from the browser. Mapbox's free
-- tier is 100,000 temporary-geocoding requests per month — going over
-- silently turns into real money. We never want that to happen by
-- accident, so we keep our own counter in Postgres and refuse to call
-- Mapbox once we cross a configurable cap (default 80,000 — 20% safety
-- margin under the 100k free tier).
--
-- The counter is intentionally tiny: one row per calendar month
-- (UTC). The atomic bump function takes a row-level lock so two
-- concurrent requests cannot both observe `used = cap - 1` and both
-- decide to proceed. Returns the new used count on success, or -1 if
-- the cap would be breached (in which case `used` is NOT incremented
-- and the API surface returns 503 to the client).
--
-- Idempotent: re-running the file is safe.

create table if not exists vitrina_geocode_usage (
  month_key   text  primary key,        -- 'YYYY-MM' UTC
  used        int   not null default 0,
  cap_when_seen int not null default 0, -- last cap value at write time, for debugging
  updated_at  timestamptz not null default now()
);

comment on table vitrina_geocode_usage is
  'Monthly request counter for the Vitrina Mapbox geocoding proxy. One row per UTC YYYY-MM. Updated atomically by vitrina_geocode_bump().';

-- Atomic increment-with-cap. Returns the new `used` value when the
-- request is permitted, or -1 when the cap would be breached.
--
-- The FOR UPDATE row lock serialises concurrent callers on the single
-- row for the current month, so the cap is honoured precisely (no
-- overshoot beyond the cap, ever).
create or replace function vitrina_geocode_bump(p_month text, p_cap int)
returns int
language plpgsql
as $$
declare
  v_used int;
begin
  insert into vitrina_geocode_usage(month_key, used, cap_when_seen)
    values (p_month, 0, p_cap)
    on conflict (month_key) do nothing;

  select used into v_used
    from vitrina_geocode_usage
    where month_key = p_month
    for update;

  if v_used >= p_cap then
    return -1;
  end if;

  update vitrina_geocode_usage
    set used = used + 1,
        cap_when_seen = p_cap,
        updated_at = now()
    where month_key = p_month;

  return v_used + 1;
end;
$$;

comment on function vitrina_geocode_bump(text, int) is
  'Atomically reserve one Mapbox geocoding request for the given UTC month, refusing past p_cap. Returns new used count, or -1 if capped.';
