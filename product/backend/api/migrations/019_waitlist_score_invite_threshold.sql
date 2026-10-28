-- 019_waitlist_score_invite_threshold.sql · Vitrina waitlist
--
-- Fix: invites now award +25 only at the 3-friend threshold, not
-- +25 per friend. The position-page climb card has always read
-- "Invite 3 friends" with a "+25" badge and three dotted slots —
-- the score function was paying out per-invite (25 × N), which
-- both contradicted the promise and overpaid by ~3× for anyone
-- who got partway through. Single threshold, one-time bump.
--
-- Mirrors the Python ``_row_score`` in routes/waitlist.py.
-- Idempotent.

create or replace function vitrina_waitlist_score(r vitrina_waitlist)
returns int language sql immutable as $$
  select (case when coalesce(r.invited_count, 0) >= 3 then 25 else 0 end)
       + (case when r.did_call      then 15 else 0 end)
       + (case when r.did_quiz      then  5 else 0 end)
       + (case when r.did_follow_ig then  2 else 0 end);
$$;
