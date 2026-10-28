-- 016_waitlist_did_follow_ig.sql · Vitrina waitlist
--
-- Adds a fourth engagement signal: did the user follow @vitrinalocalshop
-- on Instagram from the position page? Worth +2 in the queue score —
-- much smaller than invites (+25) or a founder call (+15) because it's
-- the lowest-friction action of the four, but enough to reward the tap
-- with a visible bump. See the position-page climb-card UI in
-- ``apps/vitrina/product/web/public/waitlist/index.html``.
--
-- Idempotent: re-running the file is safe.

alter table vitrina_waitlist
  add column if not exists did_follow_ig bool not null default false;

-- Update the score function to include the new +2 bump. Mirrors the
-- Python ``_row_score`` in ``routes/waitlist.py`` so the dashboard
-- list and the position re-rank stay aligned.
create or replace function vitrina_waitlist_score(r vitrina_waitlist)
returns int language sql immutable as $$
  select (coalesce(r.invited_count, 0) * 25)
       + (case when r.did_call      then 15 else 0 end)
       + (case when r.did_quiz      then  5 else 0 end)
       + (case when r.did_follow_ig then  2 else 0 end);
$$;
