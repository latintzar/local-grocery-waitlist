"""REST surface for the Vitrina engagement-ranked waitlist.

Why this exists
---------------
Decision dossier:
``apps/vitrina/company/decisions/2026-05-06-waitlist-backend-build-vs-buy.md``.
Short version: SaaS waitlist tools (Prefinery, Viral Loops, Hypeful etc.)
either don't ship the multi-action engagement scoring we want or hide it
behind a per-seat fee, and they all silo our customer list inside their
DB. We own the row, we own the scoring, we own the comms — single
``vitrina_waitlist`` table + Resend for transactional email + Viber and
WhatsApp deep-links for chat.

Endpoints
---------

Public (no auth — anyone can sign up):

==================================================  =======================================
``POST /api/waitlist/lookup``                       Phone-first auth — returns ``token`` if exists.
``POST /api/waitlist/signup``                       Create row + send confirmation + notify Peter.
``POST /api/waitlist/answer``                       Upsert one quiz answer; flip ``did_quiz`` once full.
``GET  /api/waitlist/position/{token}``             Position page on reload + share-link visits.
``POST /api/waitlist/track-call-intent``            Fire-and-forget when chat-options screen opens.
``POST /api/waitlist/track-follow-ig``              One-shot +2 bump — user opened Instagram from waitlist.
==================================================  =======================================

Admin (Bearer ``LEO_ADMIN_TOKEN`` or ``VITRINA_ADMIN_TOKEN``):

==================================================  =======================================
``GET  /api/admin/waitlist/list``                   Full row set for the founder dashboard.
``POST /api/admin/waitlist/mark-call``              Flip ``did_call`` from the dashboard checkbox.
``GET  /api/admin/waitlist/funnel``                 10-stage funnel (PostHog top-of-funnel +
                                                    Supabase bottom-of-funnel) + errors_24h.
==================================================  =======================================

The score function lives in SQL (see migration ``014_waitlist.sql``) so
the dashboard list and position re-rank read the same number.

Note on the prefix: every router in this service is mounted without
``/api/`` because Vercel rewrites strip ``/api/...`` to ``/...`` before
forwarding to the backend. The route paths below mirror what Peter
sees in the URL bar.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, Header, HTTPException, Path as PathParam, UploadFile
from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve()
_MCP_DIR = _HERE.parents[2] / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

import supabase_client  # noqa: E402

# Sibling module — community-options curator. Lives at api/community_curation.py
# so we resolve it via the parent dir on sys.path. See its module docstring
# for why curation is fire-and-forget on every quiz submit.
_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))
import community_curation  # noqa: E402

logger = logging.getLogger(__name__)


# Public router — mounted without /api/ prefix; vercel.json rewrites
# /api/waitlist/* → /waitlist/*. See merchants.py for the same pattern.
router = APIRouter(prefix="/waitlist", tags=["waitlist"])

# Admin router — same trick, /api/admin/waitlist/* → /admin/waitlist/*.
admin_router = APIRouter(prefix="/admin/waitlist", tags=["waitlist", "admin"])

TABLE = "vitrina_waitlist"
DEFAULT_CITY = "Sofia"


# ── helpers ──────────────────────────────────────────────────────────


# Sync neighbourhood resolver (signup endpoint is sync). Mapbox
# geocoding/v5 has no neighborhood data for Sofia (audit 2026-05-08
# returned empty for every probe), so the browser-side autocomplete
# can only ship lat/lng + a generic "София". This helper queries
# OSM/Nominatim using the lat/lng to fill in the proper Bulgarian
# neighbourhood label ('ж.к. Иван Вазов' / 'кв. Манастирски ливади').
# Cached process-locally so repeat resolves are free; failures are
# silent (returns whatever the user supplied) so signup never blocks.

_NB_CACHE_TTL_SEC = 24 * 60 * 60
_NB_CACHE_MAX = 4096
_nb_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_nb_cache_lock = Lock()
# Generic place names that need replacing — when the browser shipped
# one of these as `home_neighbourhood`, we treat it as "missing".
_GENERIC_NB = {"", "софия", "sofia"}


def _is_generic_nb(value: str | None) -> bool:
    return (value or "").strip().lower() in _GENERIC_NB


# ── Sofia neighbourhood canonicalization ──────────────────────────────
# OSM/Mapbox return the same place under several different strings:
#   - "ж.к. Иван Вазов" vs "Иван Вазов" vs "Ivan Vazov"  (prefix + lang)
#   - "Center" vs "Centre" vs "Център"                     (lang)
#   - "кв. Манастирски ливади" vs "ж.к. Манастирски ливади" (prefix variant)
# Without normalization these all become separate leaderboard buckets.
# We strip the prefix, casefold, look up against a curated alias map,
# and return the single canonical Bulgarian name (no prefix).
_NB_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sofia_neighbourhoods.json"
_NB_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"ж\.?\s?к\.?|"          # ж.к., жк, ж к
    r"ж\.?\s?р\.?|"          # ж.р.
    r"кв\.?|"                # кв.
    r"в\.?\s?з\.?|"          # в.з.
    r"м\.?|"                 # м. (местност)
    r"село|с\.?"             # село / с.
    r")\s+",
    re.IGNORECASE,
)


def _load_nb_aliases() -> dict[str, str]:
    """alias-key (casefolded, prefix-stripped) → canonical Bulgarian name.

    Loaded once at module import. The JSON file is the source of truth;
    edit it (not this code) to add neighbourhoods.
    """
    try:
        with open(_NB_DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("sofia_neighbourhoods.json not found at %s", _NB_DATA_PATH)
        return {}
    except Exception:
        logger.exception("sofia_neighbourhoods.json failed to parse")
        return {}
    out: dict[str, str] = {}
    for entry in data.get("neighbourhoods") or []:
        canon = (entry.get("bg") or "").strip()
        if not canon:
            continue
        # The canonical BG name itself is also a valid alias.
        keys: set[str] = {_nb_lookup_key(canon), _nb_lookup_key(entry.get("en") or "")}
        for a in entry.get("aliases") or []:
            keys.add(_nb_lookup_key(a))
        for k in keys:
            if k and k not in out:  # first definition wins (file order = priority)
                out[k] = canon
    return out


def _nb_lookup_key(s: str) -> str:
    """Strip prefix, casefold, collapse whitespace. Used both when
    building the alias map and when looking up incoming strings."""
    if not s:
        return ""
    cleaned = _NB_PREFIX_RE.sub("", s.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip().casefold()
    # Common dot-and-space variants in numbers ("Младост 1." → "Младост 1")
    cleaned = cleaned.rstrip(".")
    return cleaned


_NB_ALIASES: dict[str, str] = _load_nb_aliases()


def _normalize_sofia_neighbourhood(raw: str | None) -> str | None:
    """Coerce any inbound neighbourhood string to the curated canonical
    Cyrillic label (no prefix). Returns None for generic/empty inputs.
    Unknown names are kept but the prefix is still stripped — better
    that than letting raw OSM noise pollute the leaderboard."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or _is_generic_nb(s):
        return None
    key = _nb_lookup_key(s)
    canon = _NB_ALIASES.get(key)
    if canon:
        return canon
    # Unknown — strip the prefix, keep the rest as the user/OSM gave it.
    cleaned = _NB_PREFIX_RE.sub("", s).strip()
    return cleaned or None


def _nominatim_lookup_sync(lat: float, lng: float, lang: str = "bg") -> str | None:
    """Reverse-geocode lat/lng to a Sofia neighbourhood via OSM.

    OSM's `address.suburb` returns the canonical 'ж.к.'/'кв.'/'в.з.'
    label for every Sofia point we tested (n=12, 100% match rate).

    The ``lang`` arg is intentionally IGNORED for the OSM call: Sofia
    neighbourhoods have exactly one canonical name (Bulgarian
    Cyrillic), and translating it to "Centre" / "Lozenets" / etc.
    just creates duplicate buckets in the leaderboard. We always
    store the Cyrillic form, regardless of the UI language the user
    saw on the way in.

    Stdlib urllib + a 4s timeout — Nominatim is the fallback path,
    not the hot path, and adding httpx here would couple this module
    to the async backend stack.
    """
    if os.getenv("VITRINA_GUEST") == "1": return None
    key = f"bg|{lat:.5f},{lng:.5f}"
    now = time.time()
    with _nb_cache_lock:
        hit = _nb_cache.get(key)
        if hit and now - hit[0] < _NB_CACHE_TTL_SEC:
            _nb_cache.move_to_end(key)
            return hit[1] or None
    qs = urllib.parse.urlencode({
        "format": "json",
        "lat": f"{lat}",
        "lon": f"{lng}",
        "addressdetails": "1",
        "accept-language": "bg",
        "zoom": "16",
    })
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{qs}",
        headers={"User-Agent": "Vitrina/1.0 (peter@vitrina.bg)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as r:
            data = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        logger.info("nominatim_signup_unreachable: %s", exc)
        return None
    addr = (data.get("address") or {}) if isinstance(data, dict) else {}
    nb = (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("quarter")
        or addr.get("village")
        or addr.get("city_district")
    )
    nb = (nb or "").strip() or None
    with _nb_cache_lock:
        _nb_cache[key] = (now, nb or "")
        while len(_nb_cache) > _NB_CACHE_MAX:
            _nb_cache.popitem(last=False)
    return nb


def _resolve_neighbourhood(
    supplied: str | None, lat: float | None, lng: float | None, lang: str
) -> str | None:
    """Decide what to write to home_neighbourhood / work_neighbourhood.

    Rules:
      * If the browser already supplied a real-looking neighbourhood
        (anything that isn't 'София'/'Sofia'/empty), trust it — that
        was probably a manual / copy-paste / Mapbox-text-match value.
      * Otherwise, if we have lat/lng, ask Nominatim. Use the result
        if non-empty; otherwise keep the browser's value as-is.
    """
    cleaned = (supplied or "").strip() or None
    if not _is_generic_nb(cleaned):
        return _normalize_sofia_neighbourhood(cleaned) or cleaned
    if lat is None or lng is None:
        return cleaned
    nb = _nominatim_lookup_sync(lat, lng, lang)
    if nb:
        return _normalize_sofia_neighbourhood(nb) or nb
    return cleaned


def _public_base_url() -> str:
    return os.environ.get("VITRINA_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def _peter_admin_email() -> str:
    return os.environ.get("VITRINA_PETER_EMAIL", "").strip()


def _peter_whatsapp_number() -> str:
    """Without the leading +, wa.me format. UK number, configured per-env."""
    return os.environ.get("PETER_WHATSAPP_NUMBER", "").strip()


def _vitrina_viber_url() -> str:
    return os.environ.get(
        "VITRINA_VIBER_BUSINESS_URL",
        "",
    ).strip()


def _normalize_phone(raw: str) -> str:
    """Keep the leading + if any, strip everything else.

    Lookup matches on the full phone (with dial code) so users moving
    countries don't accidentally collide. The frontend always sends the
    full ``dial + digits`` string, which we just sanitise here.
    """
    if not raw:
        return ""
    s = raw.strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" if plus else "+") + digits if digits else ""


_REF_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Format guard for inbound `referred_by` values. We generate codes via
# `_slugify` (lowercase alphanumeric) plus an optional numeric suffix
# or a `-<token>` fallback, so the union is `[a-z0-9-]`. Cap at 64 to
# kill any oversized junk a malicious caller might send.
_REF_INBOUND_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _slugify(name: str) -> str:
    base = (name or "").strip().lower()
    base = _REF_SLUG_RE.sub("", base)
    return base or "friend"


def _sanitize_inbound_ref(raw: str | None) -> str | None:
    """Trim + format-check a `referred_by` code coming from the
    frontend. Returns the cleaned code or ``None`` if invalid/empty.
    Defends against junk querystrings, oversized payloads, and
    accidental whitespace from copy-paste links."""
    if not raw:
        return None
    candidate = str(raw).strip()
    if not candidate:
        return None
    if not _REF_INBOUND_RE.match(candidate):
        return None
    return candidate.lower()


def _generate_referral_code(sb: Any, first_name: str) -> str:
    """First-name-based with a numeric collision suffix.

    'Peter' → 'peter'. If taken, 'peter1', 'peter2', … We stop at 50 and
    fall back to 'peter-<random>' if we somehow have 50 Peters (we won't,
    but the backend shouldn't crash if we do).
    """
    base = _slugify(first_name)
    candidate = base
    for n in range(0, 50):
        if n > 0:
            candidate = f"{base}{n}"
        try:
            res = sb.table(TABLE).select("id").eq("referral_code", candidate).limit(1).execute() or []
            if not res:
                return candidate
        except Exception:  # pragma: no cover
            logger.exception("referral_code lookup failed for %s", candidate)
            break
    # Fallback — vanishingly unlikely path
    import secrets

    return f"{base}-{secrets.token_urlsafe(4).lower()}"


def _row_score(row: dict[str, Any]) -> int:
    """Mirror of ``vitrina_waitlist_score`` SQL function so we can rank
    in Python after a single ``SELECT *``. Cheaper than fetching the
    score column on every row when the function isn't computed."""
    return (
        (25 if int(row.get("invited_count") or 0) >= 3 else 0)
        + (15 if row.get("did_call") else 0)
        + (5 if row.get("did_quiz") else 0)
        + (2 if row.get("did_follow_ig") else 0)
    )


def _rank_in_city(sb: Any, city: str) -> list[dict[str, Any]]:
    """Return all rows in ``city``, sorted by (score desc, created_at asc).

    The list is the source of truth for ``position``: index + 1.
    """
    try:
        res = (
            sb.table(TABLE)
            .select("*")
            .eq("city", city)
            .execute()
        ) or []
    except Exception:
        logger.exception("rank_in_city: query failed")
        return []
    rows = list(res)
    rows.sort(key=lambda r: (-_row_score(r), r.get("created_at") or ""))
    return rows


def _position_of(rows: list[dict[str, Any]], row_id: str) -> int:
    for i, r in enumerate(rows):
        if str(r.get("id")) == str(row_id):
            return i + 1
    return 0


def _require_admin(authorization: str | None) -> None:
    """Bearer-protected admin endpoints. Accept either ``LEO_ADMIN_TOKEN``
    or the existing ``VITRINA_ADMIN_TOKEN``; this dashboard is the only
    public artefact tied to ``LEO_ADMIN_TOKEN`` so far."""
    expected = (
        os.environ.get("LEO_ADMIN_TOKEN", "").strip()
        or os.environ.get("VITRINA_ADMIN_TOKEN", "").strip()
    )
    if not expected:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")
    if not authorization:
        raise HTTPException(status_code=401, detail="admin_token_required")
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="admin_token_invalid")


# ── pydantic models ─────────────────────────────────────────────────


class LookupBody(BaseModel):
    phone: str
    city: str = DEFAULT_CITY


class SignupBody(BaseModel):
    phone: str
    city: str = DEFAULT_CITY
    email: str | None = None
    first_name: str | None = None

    home_address: str | None = None
    home_neighbourhood: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None

    work_company: str | None = None
    work_address: str | None = None
    work_neighbourhood: str | None = None
    work_lat: float | None = None
    work_lng: float | None = None

    quiz_answers: dict[str, Any] | None = None
    referred_by: str | None = None
    lang: str = "bg"


class AnswerBody(BaseModel):
    token: str
    # Either send {key, value} for a single answer, or {quiz_answers} for bulk.
    key: str | None = None
    value: Any = None
    quiz_answers: dict[str, Any] | None = None


class TrackCallBody(BaseModel):
    token: str
    channel: str = "whatsapp"
    lang: str = "bg"


class TrackFollowIgBody(BaseModel):
    token: str


class MarkCallBody(BaseModel):
    id: str = Field(..., description="vitrina_waitlist.id (uuid)")
    completed: bool = True


class LogCallBody(BaseModel):
    """Founder-dashboard call-log payload. ``notes`` is the markdown
    blob produced by the modal — typically the founder's free-text
    plus an optional speaker-labelled transcript appended below."""

    id: str = Field(..., description="vitrina_waitlist.id (uuid)")
    notes: str = ""
    completed: bool = True


class DeleteSignupBody(BaseModel):
    """Founder-dashboard hard-delete payload. The ``confirm`` string
    must match the per-row challenge phrase the dashboard asks the
    founder to retype (``delete-<firstname-or-id-prefix>-<random>``).
    Backend re-validates the prefix shape so a misclick on the wrong
    row's button cannot delete the wrong signup. The random suffix is
    just there to make muscle-memory typing impossible — its content
    is not validated server-side."""

    id: str = Field(..., description="vitrina_waitlist.id (uuid)")
    confirm: str = Field(..., min_length=8, max_length=128)


# ── public routes ────────────────────────────────────────────────────


@router.post("/lookup")
def lookup(body: LookupBody = Body(...)) -> dict[str, Any]:
    phone = _normalize_phone(body.phone)
    if not phone or len(phone) < 7:
        raise HTTPException(status_code=400, detail="phone_required")
    sb = supabase_client.get_supabase()
    try:
        res = (
            sb.table(TABLE)
            .select("id")
            .eq("city", body.city)
            .eq("phone", phone)
            .limit(1)
            .execute()
        ) or []
    except Exception:
        logger.exception("waitlist.lookup db error")
        raise HTTPException(status_code=503, detail="db_unavailable")
    rows = list(res)
    if not rows:
        return {"exists": False}
    return {"exists": True, "token": str(rows[0]["id"])}


_COMMUNITY_QIDS = {"buy", "ripped", "wish"}
_COMMUNITY_MAX_LABEL_LEN = 80


def _trim_community_label(label: str, max_len: int = 48) -> str:
    """Shorten chip labels for display — curated rows should already be
    terse, but this guards against legacy long strings and raw fallback."""
    s = (label or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


@router.get("/community_answers")
def community_answers(
    background_tasks: BackgroundTasks,
    qid: str,
    limit: int = 10,
    lang: str = "en",
) -> dict[str, Any]:
    """Top-N free-text answers other waitlist signups have given for a
    given multi-select quiz question, with frequency counts.

    The frontend surfaces these as additional chips below the preset
    options on Q1 (buy), Q2 (ripped), Q3 (wish) — so people can pick
    things their neighbours already wrote in instead of typing
    themselves.

    **Curated path (preferred).** When ``community_curation`` has
    written a row to ``vitrina_waitlist_community_curated`` for this
    qid, we serve the language-matched array straight from that row.
    The curator (Sonnet 4.6 via Anthropic API direct, fired off
    after every quiz submit) merges typos / near-duplicates,
    translates so each entry has both EN+BG labels, and drops
    offensive / nonsense / off-topic submissions. See
    ``community_curation.py`` for the full rationale.

    **Raw fallback.** Only when **no row** exists yet in
    ``vitrina_waitlist_community_curated`` for this ``qid`` (migration not
    applied, or first boot before the first curator write). Never falls
    back just because the curated array is empty — an empty curated row
    means "show no community chips", not "dump every raw answer".

    For Q2 the historical free-text strings (single string in
    quiz_answers.ripped, written before we converted to a chip group)
    are still surfaced via the raw aggregator, truncated to
    ``_COMMUNITY_MAX_LABEL_LEN`` chars.

    Read-only, no auth — these answers are aggregate, no PII.
    """
    qid = (qid or "").strip().lower()
    if qid not in _COMMUNITY_QIDS:
        return {"qid": qid, "items": []}
    lang = (lang or "en").strip().lower()
    cap = max(1, min(limit, 50))

    # Nudge curator when the stored snapshot is stale so returning users
    # (incl. signed-up-but-not-quizzed-yet) see learned options that match
    # the latest pool — not only people who triggered a submit in the last
    # few minutes.
    community_curation.schedule_refresh_if_stale(background_tasks, qid)

    # Curated path — ANY row in ``vitrina_waitlist_community_curated``
    # wins over raw aggregation, even when ``items_*`` is empty (cold
    # pool after aggressive drops). Using ``if curated and
    # curated.get("items")`` was wrong: a truthy row with ``items=[]``
    # fell through to raw and re-surfaced every dirty long free-text
    # answer from the DB — exactly the "not recuated" bug on the quiz.
    curated = community_curation.get_curated(qid, lang)
    if curated is not None:
        raw_items = curated.get("items") or []
        items_out: list[dict[str, Any]] = []
        for it in raw_items[:cap]:
            if not isinstance(it, dict):
                continue
            lbl = _trim_community_label(str(it.get("label") or ""))
            if not lbl:
                continue
            try:
                cnt = int(it.get("count") or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt < 1:
                cnt = 1
            entry: dict[str, Any] = {"label": lbl, "count": cnt}
            mf = it.get("merged_from")
            if isinstance(mf, list) and mf:
                entry["merged_from"] = mf
            items_out.append(entry)
        return {
            "qid": qid,
            "items": items_out,
            "source": "curated",
            "last_curated_at": curated.get("last_curated_at"),
        }

    # Raw fallback.
    sb = supabase_client.get_supabase()
    rows: list[dict[str, Any]] = (
        sb.table(TABLE).select("quiz_answers").execute()
    ) or []
    counts: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in rows:
        qa = r.get("quiz_answers") or {}
        if not isinstance(qa, dict):
            continue
        raw = qa.get(qid)
        values: list[str] = []
        if isinstance(raw, list):
            values = [str(v).strip() for v in raw if v]
        elif isinstance(raw, str) and raw.strip():
            values = [raw.strip()]
        for v in values:
            if not v:
                continue
            v = v[:_COMMUNITY_MAX_LABEL_LEN]
            key = v.casefold()
            entry = counts.get(key)
            if entry is None:
                counts[key] = {"label": v, "count": 1}
            else:
                entry["count"] += 1
    ranked = sorted(
        counts.values(), key=lambda e: (-e["count"], e["label"].casefold())
    )[:cap]
    for e in ranked:
        e["label"] = _trim_community_label(str(e.get("label") or ""))
    return {"qid": qid, "items": ranked, "source": "raw"}


@router.get("/leaderboard")
def leaderboard(
    token: str | None = None,
    limit: int = 8,
    city: str = DEFAULT_CITY,
) -> dict[str, Any]:
    """Top home_neighbourhood buckets for the position-screen board.

    Counts signups grouped by ``home_neighbourhood``, drops the generic
    'София' / 'Sofia' / NULL buckets (city-as-neighbourhood, useless
    for density), sorts by count DESC then name ASC, returns the top
    ``limit``. When ``token`` is provided, marks the requesting user's
    own neighbourhood with ``is_you=true`` so the client can highlight
    it. The user's neighbourhood is also returned at the top level so
    the client can render an "(ти)" badge even if it's outside the
    top-N.

    Read-only. No auth — leaderboards are public knowledge by design;
    they're the urgency lever for invite-the-block sharing.
    """
    sb = supabase_client.get_supabase()
    rows: list[dict[str, Any]] = (
        sb.table(TABLE)
        .select("home_neighbourhood")
        .eq("city", city)
        .execute()
    ) or []
    counts: dict[str, int] = {}
    for r in rows:
        raw = (r.get("home_neighbourhood") or "").strip()
        if _is_generic_nb(raw):
            continue
        # Read-path canonicalization defends the leaderboard against any
        # legacy rows that pre-date the write-path normalizer.
        nb = _normalize_sofia_neighbourhood(raw) or raw
        counts[nb] = counts.get(nb, 0) + 1
    full_sorted = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    items = full_sorted[: max(1, min(limit, 25))]

    user_nb: str | None = None
    if token:
        try:
            r = (
                sb.table(TABLE)
                .select("home_neighbourhood")
                .eq("id", token)
                .limit(1)
                .execute()
            ) or []
            if r:
                v = (r[0].get("home_neighbourhood") or "").strip()
                if v and not _is_generic_nb(v):
                    user_nb = _normalize_sofia_neighbourhood(v) or v
        except Exception:
            logger.exception("waitlist.leaderboard: user lookup failed")

    # The frontend now renders "your neighbourhood is #N" on the
    # position screen, so we tell it N directly. Computed against the
    # FULL sorted list, not the truncated `items` (the user's hood may
    # be outside the top-25 we display).
    user_rank: int | None = None
    total_neighbourhoods: int = len(full_sorted)
    if user_nb:
        for idx, (nb, _c) in enumerate(full_sorted):
            if nb == user_nb:
                user_rank = idx + 1
                break

    return {
        "items": [
            {
                "neighbourhood": nb,
                "count": c,
                "is_you": user_nb is not None and nb == user_nb,
            }
            for nb, c in items
        ],
        "user_neighbourhood": user_nb,
        "user_rank": user_rank,
        "total_neighbourhoods": total_neighbourhoods,
        "total_signups": sum(counts.values()),
    }


@router.post("/signup")
def signup(
    background_tasks: BackgroundTasks,
    body: SignupBody = Body(...),
) -> dict[str, Any]:
    phone = _normalize_phone(body.phone)
    if not phone or len(phone) < 7:
        raise HTTPException(status_code=400, detail="phone_required")
    if not body.first_name or not body.first_name.strip():
        raise HTTPException(status_code=400, detail="first_name_required")
    sb = supabase_client.get_supabase()

    # de-dupe — if (city, phone) already exists, return the existing row
    try:
        existing = (
            sb.table(TABLE)
            .select("*")
            .eq("city", body.city)
            .eq("phone", phone)
            .limit(1)
            .execute()
        ) or []
    except Exception:
        logger.exception("waitlist.signup pre-lookup failed")
        raise HTTPException(status_code=503, detail="db_unavailable")
    if existing:
        row = existing[0]
        rows = _rank_in_city(sb, body.city)
        return {
            "token": str(row["id"]),
            "position": _position_of(rows, row["id"]),
            "total": len(rows),
            "referral_code": row.get("referral_code"),
            "did_quiz": bool(row.get("did_quiz")),
            "share_link": f"{_public_base_url()}/?r={row.get('referral_code') or ''}",
            "duplicate": True,
        }

    # generate referral code from first name (with collision suffix)
    referral_code = _generate_referral_code(sb, body.first_name)

    # sanitise inbound referral. Three guards stack:
    #   1. format whitelist (kills junk + oversized payloads),
    #   2. self-referral block (no one credits themselves),
    #   3. existence check below — if the code doesn't resolve to a row,
    #      we keep the field for record-keeping but skip the +25 bump.
    referred_by = _sanitize_inbound_ref(body.referred_by)
    if referred_by and referred_by == referral_code.lower():
        referred_by = None

    did_quiz = False
    if body.quiz_answers and isinstance(body.quiz_answers, dict):
        # mark quiz done if at least 5 keys are answered (matches /waitlist questionnaire)
        filled = sum(
            1 for v in body.quiz_answers.values()
            if v not in (None, "", [], {})
        )
        did_quiz = filled >= 5

    # Enrich neighbourhoods server-side. The browser-side Mapbox path
    # cannot fill home_neighbourhood for Sofia (Mapbox geocoding/v5 has
    # no neighborhood data for Sofia — see the 2026-05-08 audit) so we
    # do it here using OSM/Nominatim. One sync call per signup, cached
    # locally, fails open.
    home_nb = _resolve_neighbourhood(
        body.home_neighbourhood, body.home_lat, body.home_lng, body.lang
    )
    work_nb = _resolve_neighbourhood(
        body.work_neighbourhood, body.work_lat, body.work_lng, body.lang
    )

    payload: dict[str, Any] = {
        "city": body.city,
        "phone": phone,
        "email": (body.email or "").strip() or None,
        "first_name": body.first_name.strip(),
        "home_address": body.home_address,
        "home_neighbourhood": home_nb,
        "home_lat": body.home_lat,
        "home_lng": body.home_lng,
        "work_company": body.work_company,
        "work_address": body.work_address,
        "work_neighbourhood": work_nb,
        "work_lat": body.work_lat,
        "work_lng": body.work_lng,
        "referral_code": referral_code,
        "referred_by": referred_by,
        "did_quiz": did_quiz,
        "quiz_answers": body.quiz_answers or {},
    }
    try:
        inserted = sb.table(TABLE).insert(payload).execute()
    except Exception as exc:
        logger.exception("waitlist.signup insert failed")
        raise HTTPException(status_code=503, detail="db_insert_failed") from exc
    if not inserted:
        raise HTTPException(status_code=500, detail="db_insert_empty")
    row = inserted[0] if isinstance(inserted, list) else inserted

    # increment the referrer's invited_count if they sent us, then fire
    # the friend-joined email so the referrer sees the avatar grid fill.
    # `referred_by` is already sanitised above (whitelist + self-ref
    # guard), so we only have to defend against the lookup missing.
    if referred_by:
        try:
            ref = (
                sb.table(TABLE)
                .select("*")
                .eq("referral_code", referred_by)
                .limit(1)
                .execute()
            ) or []
            if ref and str(ref[0].get("id")) != str(row.get("id")):
                referrer = ref[0]
                rid = referrer["id"]
                cur = int(referrer.get("invited_count") or 0)
                sb.table(TABLE).update({"invited_count": cur + 1}).eq("id", rid).execute()
                # Re-fetch so the email reads the post-update count (matters
                # for the avatar fill state in _render_branded_email).
                referrer["invited_count"] = cur + 1
                try:
                    _send_friend_joined(
                        referrer_row=referrer,
                        invitee_first_name=body.first_name.strip(),
                        lang=referrer.get("lang") or body.lang,
                    )
                except Exception:
                    logger.exception("waitlist.signup: friend-joined email failed")
        except Exception:
            logger.exception("waitlist.signup: failed to increment referrer count")

    rows = _rank_in_city(sb, body.city)
    position = _position_of(rows, row["id"])
    total = len(rows)
    share_link = f"{_public_base_url()}/?r={referral_code}"

    # confirmation + Peter notification — fire-and-forget, mustn't break signup
    try:
        _send_signup_confirmation(
            row=row,
            position=position,
            share_link=share_link,
            lang=body.lang,
        )
    except Exception:
        logger.exception("waitlist.signup: confirmation email transport error")
    try:
        _notify_peter_new_signup(row=row, position=position)
    except Exception:
        logger.exception("waitlist.signup: peter notification transport error")

    # Fire-and-forget: re-curate the community pool for any qid this
    # signup just contributed to. Same debounced path the /answer
    # endpoint uses, and a no-op when no community-input qids were
    # touched (cold visitors who skipped the inline quiz).
    community_curation.schedule_curation_for_answers(
        background_tasks, body.quiz_answers
    )

    return {
        "token": str(row["id"]),
        "position": position,
        "total": total,
        "referral_code": referral_code,
        "did_quiz": did_quiz,
        "share_link": share_link,
        "duplicate": False,
    }


@router.post("/answer")
def answer(
    background_tasks: BackgroundTasks,
    body: AnswerBody = Body(...),
) -> dict[str, Any]:
    sb = supabase_client.get_supabase()
    try:
        cur = sb.table(TABLE).select("quiz_answers").eq("id", body.token).limit(1).execute() or []
    except Exception:
        logger.exception("waitlist.answer pre-fetch failed")
        raise HTTPException(status_code=503, detail="db_unavailable")
    if not cur:
        raise HTTPException(status_code=404, detail="not_found")
    answers = dict(cur[0].get("quiz_answers") or {})
    if body.quiz_answers:
        for k, v in body.quiz_answers.items():
            answers[str(k)] = v
    elif body.key is not None:
        answers[body.key] = body.value
    else:
        raise HTTPException(status_code=422, detail="quiz_answers_or_key_required")

    filled = sum(1 for v in answers.values() if v not in (None, "", [], {}))
    did_quiz = filled >= 5
    was_done = bool(cur[0].get("did_quiz") if isinstance(cur, list) and cur else False)
    try:
        sb.table(TABLE).update({"quiz_answers": answers, "did_quiz": did_quiz}).eq("id", body.token).execute()
    except Exception:
        logger.exception("waitlist.answer update failed")
        raise HTTPException(status_code=503, detail="db_update_failed")
    # Quiz just transitioned 0→1: send the +5 congrats email. Refetch
    # the row so the email payload sees the updated did_quiz value.
    if did_quiz and not was_done:
        try:
            full = sb.table(TABLE).select("*").eq("id", body.token).limit(1).execute() or []
            if full:
                _send_quiz_completed(row=full[0])
        except Exception:
            logger.exception("waitlist.answer: quiz-completed email failed")

    # Fire-and-forget: re-curate the community pool for any qid this
    # answer touched. Debounced internally to ≤1 LLM call/min/qid.
    # User response has already been computed; this runs after send.
    community_curation.schedule_curation_for_answers(
        background_tasks, body.quiz_answers if body.quiz_answers else {body.key: body.value} if body.key else None
    )
    return {"ok": True, "did_quiz": did_quiz, "answers": answers}


@router.get("/position/{token}")
def position(token: str = PathParam(...)) -> dict[str, Any]:
    sb = supabase_client.get_supabase()
    try:
        cur = sb.table(TABLE).select("*").eq("id", token).limit(1).execute() or []
    except Exception:
        logger.exception("waitlist.position fetch failed")
        raise HTTPException(status_code=503, detail="db_unavailable")
    if not cur:
        raise HTTPException(status_code=404, detail="not_found")
    row = cur[0]
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    pos = _position_of(rows, token)
    behind = max(0, len(rows) - pos)
    invited_names = _fetch_invited_first_names(sb, row.get("referral_code"))
    return {
        "position": pos,
        "total": len(rows),
        "behind": behind,
        "score": _row_score(row),
        "first_name": row.get("first_name"),
        "referral_code": row.get("referral_code"),
        "invited_count": int(row.get("invited_count") or 0),
        "invited_names": invited_names,
        "did_call": bool(row.get("did_call")),
        "did_quiz": bool(row.get("did_quiz")),
        "did_follow_ig": bool(row.get("did_follow_ig")),
    }


def _fetch_invited_first_names(sb: Any, referral_code: str | None) -> list[str]:
    """First names of users who signed up using ``referral_code``,
    oldest-first (so the avatar slots fill in invitation order). The
    position page renders these as initial-glyphs in the Monzo-style
    invite card; the friend-joined email shows the same fill state so
    the email and webpage stay in sync.
    """
    code = (referral_code or "").strip()
    if not code:
        return []
    try:
        invs = (
            sb.table(TABLE)
            .select("first_name, created_at")
            .eq("referred_by", code)
            .execute()
        ) or []
    except Exception:
        logger.exception("waitlist.position: invited-names lookup failed")
        return []
    invs = list(invs)
    invs.sort(key=lambda r: r.get("created_at") or "")
    return [
        (r.get("first_name") or "").strip()
        for r in invs
        if (r.get("first_name") or "").strip()
    ]


@router.post("/track-call-intent")
def track_call_intent(body: TrackCallBody = Body(...)) -> dict[str, Any]:
    """Fire-and-forget: user just opened the WhatsApp/Viber chat-options
    screen. Notify Peter so he sees the inbound traffic even if the user
    abandons before sending a message."""
    sb = supabase_client.get_supabase()
    try:
        cur = sb.table(TABLE).select("*").eq("id", body.token).limit(1).execute() or []
    except Exception:
        logger.exception("waitlist.track-call-intent fetch failed")
        return {"ok": False}
    if not cur:
        return {"ok": False, "error": "not_found"}
    row = cur[0]
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    pos = _position_of(rows, body.token)
    # Persist the intent so the founder dashboard's Call column can
    # surface "wants a call" badges. We overwrite on each tap (no
    # history) — the dashboard only needs the most recent signal.
    try:
        sb.table(TABLE).update(
            {
                "call_intent_at": datetime.now(timezone.utc).isoformat(),
                "call_intent_channel": (body.channel or "").strip()[:32] or None,
            }
        ).eq("id", body.token).execute()
    except Exception:
        logger.exception("waitlist.track-call-intent: persist failed")
    try:
        _notify_peter_call_requested(row=row, position=pos, channel=body.channel, lang=body.lang)
    except Exception:
        logger.exception("waitlist.track-call-intent: peter notify failed")
    return {"ok": True}


@router.post("/track-follow-ig")
def track_follow_ig(body: TrackFollowIgBody = Body(...)) -> dict[str, Any]:
    """One-shot +2 bump when the user opens our Instagram (@vitrinalocalshop)
    from the position page's "Follow us on Instagram" climb-card.

    Idempotent: subsequent calls with the same token are no-ops (the
    flag is already true). Returns the post-flip position so the
    frontend can re-render the queue number without a separate
    ``/position`` round-trip.
    """
    sb = supabase_client.get_supabase()
    try:
        cur = sb.table(TABLE).select("*").eq("id", body.token).limit(1).execute() or []
    except Exception:
        logger.exception("waitlist.track-follow-ig fetch failed")
        return {"ok": False}
    if not cur:
        return {"ok": False, "error": "not_found"}
    row = cur[0]
    was_done = bool(row.get("did_follow_ig"))
    if not was_done:
        try:
            sb.table(TABLE).update({"did_follow_ig": True}).eq("id", body.token).execute()
            row["did_follow_ig"] = True
        except Exception:
            logger.exception("waitlist.track-follow-ig update failed")
            raise HTTPException(status_code=503, detail="db_update_failed")
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    return {
        "ok": True,
        "did_follow_ig": True,
        "position": _position_of(rows, body.token),
        "score": _row_score(row),
        "was_already_done": was_done,
    }


# ── admin routes ─────────────────────────────────────────────────────


@admin_router.get("/list")
def admin_list(authorization: str | None = Header(None)) -> dict[str, Any]:
    _require_admin(authorization)
    sb = supabase_client.get_supabase()
    try:
        res = sb.table(TABLE).select("*").execute() or []
    except Exception:
        logger.exception("admin_list db error")
        raise HTTPException(status_code=503, detail="db_unavailable")
    raw = list(res)
    # group by city → rank → flatten with position injected
    by_city: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        by_city.setdefault(r.get("city") or DEFAULT_CITY, []).append(r)
    out: list[dict[str, Any]] = []
    for city, rs in by_city.items():
        rs.sort(key=lambda r: (-_row_score(r), r.get("created_at") or ""))
        for i, r in enumerate(rs):
            r2 = dict(r)
            r2["position"] = i + 1
            r2["score"] = _row_score(r)
            out.append(r2)
    out.sort(key=lambda r: (r.get("city") or "", r.get("position") or 999999))
    return {"rows": out, "count": len(out)}


@admin_router.post("/mark-call")
def admin_mark_call(
    body: MarkCallBody = Body(...),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Legacy ergonomics: just toggle did_call without notes. Kept for
    backwards compat; the dashboard now uses /log-call which carries
    notes + transcript."""
    _require_admin(authorization)
    return _apply_call_log(body.id, completed=bool(body.completed), notes=None)


@admin_router.post("/log-call")
def admin_log_call(
    body: LogCallBody = Body(...),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Persist call notes from the founder-dashboard modal + flip
    did_call. Reshuffle of the queue is automatic — every read goes
    through ``_rank_in_city`` which re-sorts on each call. The +15
    congrats email fires once on the 0→1 transition."""
    _require_admin(authorization)
    return _apply_call_log(
        body.id,
        completed=bool(body.completed),
        notes=(body.notes or "").strip() or None,
    )


def _apply_call_log(row_id: str, *, completed: bool, notes: str | None) -> dict[str, Any]:
    sb = supabase_client.get_supabase()
    prev_done = False
    try:
        prev = sb.table(TABLE).select("did_call").eq("id", row_id).limit(1).execute() or []
        if prev:
            prev_done = bool(prev[0].get("did_call"))
    except Exception:
        logger.exception("log-call prev-fetch failed for %s", row_id)
    update: dict[str, Any] = {"did_call": completed}
    if notes is not None:
        update["call_notes"] = notes
        update["call_logged_at"] = datetime.now(timezone.utc).isoformat()
    elif completed and not prev_done:
        # Tick from the legacy /mark-call path — record the timestamp
        # too even though no notes were supplied.
        update["call_logged_at"] = datetime.now(timezone.utc).isoformat()
    try:
        sb.table(TABLE).update(update).eq("id", row_id).execute()
    except Exception:
        logger.exception("log-call update failed for %s", row_id)
        raise HTTPException(status_code=503, detail="db_update_failed")
    if completed and not prev_done:
        try:
            full = sb.table(TABLE).select("*").eq("id", row_id).limit(1).execute() or []
            if full:
                _send_call_completed(row=full[0])
        except Exception:
            logger.exception("log-call: call-completed email failed")
    # Re-fetch the row + post-shuffle position so the dashboard can
    # paint the new ordering without a second round-trip.
    try:
        latest = sb.table(TABLE).select("*").eq("id", row_id).limit(1).execute() or []
        row = latest[0] if latest else {}
    except Exception:
        row = {}
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY) if row else []
    return {
        "ok": True,
        "id": row_id,
        "position": _position_of(rows, row_id) if rows else 0,
        "score": _row_score(row) if row else 0,
        "did_call": bool(row.get("did_call")) if row else completed,
        "call_logged_at": row.get("call_logged_at") if row else None,
    }


@admin_router.post("/delete")
def admin_delete(
    body: DeleteSignupBody = Body(...),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Hard-delete a waitlist signup. Used by the founder dashboard's
    "Delete signup" affordance after a typed-word confirmation modal.

    Server-side guard: the ``confirm`` string must start with
    ``delete-<key>-`` where ``<key>`` is the row's first_name (lowercased
    ASCII slug, falling back to the first 6 chars of the row id when
    first_name is empty). This prevents a misclick on row B's delete
    button from accidentally deleting row A — the challenge phrase the
    dashboard renders is keyed off the row, so retyping the WRONG phrase
    cannot match a different row by chance.
    """
    _require_admin(authorization)
    sb = supabase_client.get_supabase()
    try:
        cur = (
            sb.table(TABLE)
            .select("id, first_name, phone")
            .eq("id", body.id)
            .limit(1)
            .execute()
            or []
        )
    except Exception:
        logger.exception("admin_delete: fetch failed for %s", body.id)
        raise HTTPException(status_code=503, detail="db_unavailable")
    if not cur:
        raise HTTPException(status_code=404, detail="not_found")
    row = cur[0]

    # Compute the same key the frontend used to build the challenge.
    raw_name = (row.get("first_name") or "").strip().lower()
    slug = "".join(c if c.isalnum() else "-" for c in raw_name).strip("-")
    key = slug or str(row.get("id") or "")[:6].lower()
    expected_prefix = f"delete-{key}-"
    if not body.confirm.strip().lower().startswith(expected_prefix):
        raise HTTPException(status_code=422, detail="confirm_phrase_mismatch")

    try:
        sb.table(TABLE).delete().eq("id", body.id).execute()
    except Exception:
        logger.exception("admin_delete: delete failed for %s", body.id)
        raise HTTPException(status_code=503, detail="db_delete_failed")
    logger.info("admin_delete: removed waitlist row id=%s phone=%s", body.id, row.get("phone"))
    return {"ok": True, "id": body.id}


@admin_router.post("/transcribe")
async def admin_transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Browser uploads a recorded blob, we run STT (Groq Whisper via the
    voice package) and an LLM disambiguation pass that splits Peter vs
    Caller into a Markdown block. The dashboard appends this Markdown to
    the notes textarea so the founder can edit before saving.

    Why an LLM pass: Groq Whisper has no built-in diarization. Two
    speakers in a phone call is a tractable disambiguation task for any
    cheap LLM — context (questions vs answers, intro lines, who's
    asking about delivery vs who's answering) gets you ~95% accurate
    speaker tagging on a 5-minute call.
    """
    _require_admin(authorization)
    try:
        audio = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="upload_read_failed") from exc
    if not audio:
        raise HTTPException(status_code=400, detail="empty_audio")
    if len(audio) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio_too_large")

    content_type = (file.content_type or "audio/webm").split(";")[0].strip()
    try:
        from voice import transcribe as voice_transcribe  # noqa: WPS433

        raw = await voice_transcribe(
            audio,
            content_type=content_type,
            language=language,
            polish=False,
        )
        plain_text = (raw.text or "").strip()
    except Exception:
        logger.exception("admin_transcribe: STT failed")
        raise HTTPException(status_code=502, detail="stt_failed")
    if not plain_text:
        return {"transcript": "", "markdown": ""}

    markdown = await _diarize_two_speaker(plain_text)
    return {"transcript": plain_text, "markdown": markdown}


@admin_router.get("/funnel")
def admin_funnel(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Full-funnel view for the founder dashboard.

    Merges two data sources:
    - PostHog HogQL (stages 1-7, top-of-funnel): page views, CTA clicks,
      and each waitlist step reached.  Falls back gracefully to zeros when
      ``POSTHOG_PROJECT_TOKEN`` / ``POSTHOG_PROJECT_ID`` are not set.
    - Supabase ``vitrina_waitlist`` (stages 8-10, bottom-of-funnel): actual
      row-level signals (signup submitted, quiz completed, call logged).

    Each stage carries ``{name, event, count, pct}`` where ``pct`` is the
    conversion rate vs the first (page-view) stage.
    """
    _require_admin(authorization)
    sb = supabase_client.get_supabase()

    # bottom-of-funnel: Supabase row-level signals (always available)
    try:
        rows = list(
            sb.table(TABLE)
            .select("phone, did_quiz, did_call")
            .execute()
            or []
        )
    except Exception:
        logger.exception("admin_funnel: supabase query failed")
        rows = []

    total_signups = sum(1 for r in rows if r.get("phone"))
    total_quiz = sum(1 for r in rows if r.get("did_quiz"))
    total_call = sum(1 for r in rows if r.get("did_call"))

    # top-of-funnel: PostHog event counts (graceful if env vars absent)
    ph_stages = _posthog_funnel_top()
    errors_24h = _posthog_errors_24h()

    stages: list[dict[str, Any]] = [
        *ph_stages,
        {"name": "Signup submitted",         "event": "wl.signup_complete", "count": total_signups},
        {"name": "Quiz completed (+5 pts)",  "event": "wl.quiz_complete",   "count": total_quiz},
        {"name": "Call with Peter (+15 pts)", "event": "wl.call_logged",    "count": total_call},
    ]

    # convert vs first stage
    top = stages[0]["count"] if stages and stages[0]["count"] > 0 else 1
    for s in stages:
        s["pct"] = round(s["count"] / top * 100, 1) if top else 0.0

    return {"stages": stages, "errors_24h": errors_24h}


# ── PostHog helpers ─────────────────────────────────────────────────

_POSTHOG_TOP_STAGES: list[tuple[str, str]] = [
    ("Home page view (vitrina.bg)",        "$pageview.home"),
    ("Get-access CTA clicked",              "home.cta_clicked"),
    ("Waitlist page reached",               "$pageview.waitlist"),
    ("Entered name (step 2)",               "wl.step_reached.name"),
    ("Entered email (step 3)",              "wl.step_reached.email"),
    ("Home address pinned (step 4)",        "wl.step_reached.home"),
    ("Work address / skipped (step 5)",     "wl.step_reached.work"),
]

_POSTHOG_HQL = """
SELECT
    countIf(
        event = '$pageview'
        AND NOT (properties.$current_url LIKE '%/waitlist%'
              OR properties.$current_url LIKE '%/founder%')
    ) AS home_views,
    countIf(event = 'home.cta_clicked')                                   AS cta_clicks,
    countIf(event = '$pageview' AND properties.$current_url LIKE '%/waitlist%') AS waitlist_views,
    countIf(event = 'wl.step_reached' AND properties.step = 'name')      AS wl_name,
    countIf(event = 'wl.step_reached' AND properties.step = 'email')     AS wl_email,
    countIf(event = 'wl.step_reached' AND properties.step = 'home')      AS wl_home,
    countIf(event = 'wl.step_reached' AND properties.step = 'work')      AS wl_work
FROM events
WHERE timestamp >= now() - interval 7 day
"""


def _posthog_base_url() -> str:
    return os.environ.get("POSTHOG_HOST", "https://eu.posthog.com").rstrip("/")


def _posthog_funnel_top() -> list[dict[str, Any]]:
    """Return per-stage event counts from PostHog for the last 7 days.

    Returns zeros for all stages if env vars are absent, so the founder
    dashboard still renders (with empty bars) before analytics are wired.
    """
    token = os.environ.get("POSTHOG_PROJECT_TOKEN", "").strip()
    project_id = os.environ.get("POSTHOG_PROJECT_ID", "").strip()

    empty = [{"name": n, "event": e, "count": 0} for n, e in _POSTHOG_TOP_STAGES]

    if not token or not project_id:
        return empty

    try:
        from core import http_client_sync  # noqa: WPS433

        with http_client_sync(timeout=12) as client:
            resp = client.post(
                f"{_posthog_base_url()}/api/projects/{project_id}/query/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"query": {"kind": "HogQLQuery", "query": _POSTHOG_HQL}},
            )
            if resp.status_code != 200:
                logger.warning(
                    "posthog_funnel_top: unexpected %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return empty

            data = resp.json()
            results = data.get("results") or []
            row = results[0] if results else []
            counts = [int(v or 0) for v in row]
            while len(counts) < len(_POSTHOG_TOP_STAGES):
                counts.append(0)

            return [
                {"name": name, "event": evt, "count": counts[i]}
                for i, (name, evt) in enumerate(_POSTHOG_TOP_STAGES)
            ]
    except Exception:
        logger.exception("_posthog_funnel_top: query failed")
        return empty


def _posthog_errors_24h() -> int:
    """Return the number of $exception events in the last 24 h."""
    token = os.environ.get("POSTHOG_PROJECT_TOKEN", "").strip()
    project_id = os.environ.get("POSTHOG_PROJECT_ID", "").strip()
    if not token or not project_id:
        return 0

    hql = "SELECT count() FROM events WHERE event = '$exception' AND timestamp >= now() - interval 1 day"
    try:
        from core import http_client_sync  # noqa: WPS433

        with http_client_sync(timeout=8) as client:
            resp = client.post(
                f"{_posthog_base_url()}/api/projects/{project_id}/query/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"query": {"kind": "HogQLQuery", "query": hql}},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or []
                return int((results[0][0] if results else 0) or 0)
    except Exception:
        logger.exception("_posthog_errors_24h: query failed")
    return 0


async def _diarize_two_speaker(transcript: str) -> str:
    """Ask the shared LLM to split a one-blob transcript into a
    speaker-tagged Markdown block. Failure mode: return the raw
    transcript wrapped in a single ``**Caller:**`` block, so the
    dashboard always has *something* useful to paste."""
    fallback = f"**Caller:** {transcript.strip()}"
    try:
        from llm import chat as llm_chat  # noqa: WPS433
    except Exception:
        logger.exception("_diarize_two_speaker: llm import failed")
        return fallback
    prompt = (
        "You are formatting a phone call transcript for an internal CRM. "
        "Two speakers are on the line:\n"
        "  • **Peter** — the founder of Vitrina (he asks the questions, "
        "introduces himself, talks about producers, deliveries, the waitlist).\n"
        "  • **Caller** — a person on the Sofia waitlist (they answer "
        "Peter's questions, talk about how they shop, what they buy, "
        "where they live).\n\n"
        "Reformat the transcript below into Markdown with one speaker per "
        "turn. Use exactly this shape, blank line between turns:\n\n"
        "**Peter:** their words here\n\n"
        "**Caller:** their words here\n\n"
        "Rules:\n"
        "- Preserve every word; do NOT summarize or paraphrase.\n"
        "- Fix obvious punctuation/capitalization but leave wording alone.\n"
        "- Keep the original language (Bulgarian or English).\n"
        "- If you genuinely can't tell who's speaking, default to **Caller:**.\n"
        "- Output Markdown only, no preamble.\n\n"
        f"Transcript:\n\n{transcript}"
    )
    try:
        resp = await llm_chat([{"role": "user", "content": prompt}], tier="fast")
        text = (getattr(resp, "text", None) or "").strip()
        return text or fallback
    except Exception:
        logger.exception("_diarize_two_speaker: llm call failed")
        return fallback


# ── Resend transports — confirmation + Peter notifications ───────────


def _resend_send(*, to: str, subject: str, html: str, text: str, tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Inline copy of the merchant_emails transport — keeps this route
    independent of the merchant flow's lifetime decisions."""
    if os.getenv("VITRINA_GUEST") == "1": return {"ok": False, "error": "guest_external_actions_disabled"}
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("waitlist email: RESEND_API_KEY missing — skipping send to %s", to)
        return {"ok": False, "error": "resend_api_key_missing"}
    payload: dict[str, Any] = {
        "from": os.environ.get("VITRINA_WAITLIST_FROM", "Vitrina <hello@vitrina.bg>"),
        "to": [to],
        "reply_to": os.environ.get("VITRINA_EMAIL_REPLY_TO", "hello@vitrina.bg"),
        "subject": subject,
        "html": html,
        "text": text,
    }
    if tags:
        payload["tags"] = tags
    try:
        from core import http_client_sync

        with http_client_sync(timeout=20) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info("waitlist email ok | to=%s subject=%s id=%s", to, subject[:60], data.get("id"))
            return {"ok": True, "id": data.get("id")}
        logger.warning("waitlist email resend error | status=%s body=%s", resp.status_code, resp.text[:300])
        return {"ok": False, "error": f"resend_{resp.status_code}"}
    except Exception:
        logger.exception("waitlist email transport error to=%s", to)
        return {"ok": False, "error": "transport_error"}


# ── branded email template ──────────────────────────────────────────
#
# All four user-facing emails (welcome, friend-joined, quiz-done,
# call-done) share one HTML skeleton so the email reads as the same
# product as the /waitlist position page. The skeleton includes:
#
#   1. Vitrina logo (PNG) + Sofia eyebrow at the top.
#   2. Optional headline + intro paragraph.
#   3. Position card (big tabular-numerals position number + behind
#      count + the same three avatar slots Monzo-style as the web page).
#   4. Three climb-action rows (Invite / Call / Quiz) with the +25/+15/+5
#      bumps anchored on the right, plus checkmarks once a row is done.
#   5. Share link + WhatsApp / Viber CTAs.
#   6. Footer signed by the team.
#
# Web fonts can't be relied on in email — Archivo's not a webmail
# default, so we fall back to Helvetica/Arial. Every layout rule is
# inline (table-based) for Outlook compatibility. Colours mirror the
# brand tokens: #0e1a14 (ink), #ee6c2c (rust), #d8c057 (gold),
# #f5f7f2 (paper), #25d366 (WhatsApp), #7360f2 (Viber).


def _email_logo_url() -> str:
    return f"{_public_base_url()}/brand-assets/vitrina-logo.png"


def _position_link(token: str | None, step: str) -> str:
    """Deep link into the /waitlist SPA at a specific step, with the
    user's token attached so they land on the position page already
    authenticated (same shape the page reads in index.html: ``?t=&step=``).

    Used by the climb-action cards in transactional emails so each card
    routes to the *exact* step the equivalent button on the live page
    would take the user to — Invite -> position (which renders the
    invite card inline), Call -> chat options, Quiz -> first quiz
    question. Single source of truth for the user, whether they tap in
    the email or open the page directly.
    """
    base = _public_base_url().rstrip("/")
    qs_token = f"t={_url_encode(token)}&" if token else ""
    return f"{base}/waitlist?{qs_token}step={_url_encode(step)}"


def _e(s: str | None) -> str:
    """HTML-escape any user-controllable string before inlining."""
    return _html_escape(s or "")


def _render_avatar_slots_html(invited_names: list[str]) -> str:
    """Three-circle Monzo-style strip: filled circles for each invitee
    (showing their initial), dashed empty circles for the rest. We cap
    visually at three; if invited > 3 we show a small "+N" pill.
    """
    safe = [n.strip() for n in (invited_names or []) if isinstance(n, str) and n.strip()]
    cells: list[str] = []
    for i in range(3):
        if i < len(safe):
            initial = _e(safe[i][:1].upper())
            cells.append(
                '<td align="center" valign="middle" width="76" height="76" '
                'style="width:76px;height:76px;background:#d8c057;border:2px solid #d8c057;'
                'border-radius:38px;font-family:Helvetica,Arial,sans-serif;font-weight:900;'
                f'font-size:26px;color:#0e1a14;line-height:1;">{initial}</td>'
            )
        else:
            cells.append(
                '<td align="center" valign="middle" width="76" height="76" '
                'style="width:76px;height:76px;background:transparent;border:2px dashed rgba(255,255,255,0.4);'
                'border-radius:38px;font-family:Helvetica,Arial,sans-serif;font-weight:900;'
                'font-size:26px;color:rgba(255,255,255,0.4);line-height:1;">·</td>'
            )
    extra = ""
    if len(safe) > 3:
        extra = (
            '<div style="display:inline-block;margin-top:10px;padding:4px 10px;border-radius:12px;'
            'background:rgba(255,255,255,0.14);color:#ffffff;'
            f'font-family:\'JetBrains Mono\',Menlo,monospace;font-size:11px;font-weight:700;'
            f'letter-spacing:0.04em;">+{len(safe) - 3}</div>'
        )
    return (
        '<table role="presentation" cellspacing="14" cellpadding="0" border="0" '
        'align="center" style="margin:6px auto 14px;"><tr>'
        + "".join(cells)
        + f"</tr></table>{extra}"
    )


def _strings(lang: str) -> dict[str, str]:
    """Email copy bundle. Keys mirror the /waitlist page where useful so
    a Bulgarian user reads the email and the page in identical voice."""
    bg = (lang or "bg").lower().startswith("bg")
    if bg:
        return {
            "lang": "bg",
            "eyebrow": "София · чакаща листа",
            "spot_lbl": "Твоето място в списъка за София",
            "behind_suffix": "хора са след теб в опашката.",
            "climb_heading": "Качи се нагоре в опашката",
            "invite_title": "Покани 3 приятели",
            "invite_sub": "Всеки приятел, който се запише с твоя линк, те качва с 25 места.",
            "invite_done": "Поканени!",
            "call_title": "Кратък разговор с Питър",
            "call_done": "Готово",
            "quiz_title": "60-секунден вкусов тест",
            "quiz_done": "Готово",
            "bump_25": "+25",
            "bump_15": "+15",
            "bump_5": "+5",
            "share_lbl": "Твоят линк за приятели",
            "wa": "Сподели в WhatsApp",
            "viber": "Сподели във Viber",
            "share_body": "Записан/а съм в чакащата листа на Vitrina за София. Използвай линка ми, за да се качим и двамата:",
            "open_page": "Отвори страницата на чакащата листа",
            "sign": "— екипът на Vitrina",
            "footer": "vitrina.bg · София",
            # Regulator-friendly footer block. Status (no money taken
            # yet), data-rights (GDPR Art. 13/17 reminder), legal
            # entity. Mirrors the privacy + terms modal on /drop-01-v13
            # so a regulator comparing the two surfaces sees consistent
            # disclosures.
            "legal_status": "Това е безплатно записване в чакваща листа. Не събираме плащания и не сме сключили договор с теб. Ще те таксуваме само ако някога потвърдиш поръчка в групова доставка, която достигне минимума си.",
            "legal_data": "Използваме твоя имейл и телефон само за да ти съобщаваме мястото в опашката и кога отваря доставка в твоя квартал. За изтриване на данните пиши на",
            "legal_entity": "Leo Casa Ltd (оперира Vitrina) · регистрирано в Обединеното кралство · в очакване на регистрациите БАБХ + КЗП в България",
            "unsubscribe": "Отписване",
            "unsubscribe_subject": "Отпиши ме от чакващата листа на Vitrina",
        }
    return {
        "lang": "en",
        "eyebrow": "Sofia · waitlist",
        "spot_lbl": "Your spot on the Sofia waitlist",
        "behind_suffix": "people are behind you in the queue.",
        "climb_heading": "Move up in the queue",
        "invite_title": "Invite 3 friends",
        "invite_sub": "Each friend who signs up with your link bumps you up by 25.",
        "invite_done": "Invites sent!",
        "call_title": "Have a quick chat with Peter",
        "call_done": "Done",
        "quiz_title": "60-second taste quiz",
        "quiz_done": "Done",
        "bump_25": "+25",
        "bump_15": "+15",
        "bump_5": "+5",
        "share_lbl": "Your link for friends",
        "wa": "Share on WhatsApp",
        "viber": "Share on Viber",
        "share_body": "I'm on the Sofia waitlist for Vitrina. Use my link so we both move up:",
        "open_page": "Open my waitlist page",
        "sign": "— the Vitrina team",
        "footer": "vitrina.bg · Sofia",
        "legal_status": "This is a free waitlist signup. We don't take any payment and there's no contract between us. We'll only ever charge you if you actively confirm an order in a group drop that reaches its minimum.",
        "legal_data": "We use your email and phone only to tell you your place in the queue and when a drop opens in your area. To delete your data, email",
        "legal_entity": "Leo Casa Ltd (operating Vitrina) · UK-incorporated · pending Bulgarian БАБХ + КЗП registrations",
        "unsubscribe": "Unsubscribe",
        "unsubscribe_subject": "Unsubscribe me from the Vitrina waitlist",
    }


def _render_climb_row(*, label: str, bump: str, done: bool, done_label: str, href: str | None = None) -> str:
    """One row of the climb-actions table. Bump always pinned right.

    When ``href`` is set, the entire row becomes a link to the matching
    step on the /waitlist page so tapping the Call / Quiz card in the
    email lands the user on the same screen the equivalent button on
    the page would open. Done rows still link (so a user can re-open
    a completed quiz to review answers) but lose the active styling."""
    bg = "#eaece4" if done else "#ffffff"
    border = "rgba(28,28,26,0.28)" if done else "#0e1a14"
    fg = "rgba(14,26,20,0.62)" if done else "#0e1a14"
    bump_color = "#3f7e36" if done else "#ee6c2c"
    bump_text = (f"\u2713 {done_label}") if done else bump
    inner = (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        f'style="background:{bg};border:1.5px solid {border};">'
        f'<tr>'
        f'<td style="padding:14px 16px;font-family:Helvetica,Arial,sans-serif;font-weight:800;'
        f'font-size:15px;color:{fg};line-height:1.3;">{_e(label)}</td>'
        f'<td align="right" style="padding:14px 16px;font-family:\'JetBrains Mono\',Menlo,monospace;'
        f'font-weight:800;font-size:13px;color:{bump_color};white-space:nowrap;">{_e(bump_text)}</td>'
        f'</tr></table>'
    )
    if href:
        inner = f'<a href="{_e(href)}" style="display:block;text-decoration:none;color:inherit;">{inner}</a>'
    return f'<tr><td style="padding:0 0 10px;">{inner}</td></tr>'


def _render_branded_email(
    *,
    row: dict[str, Any],
    position: int,
    behind: int,
    invited_names: list[str],
    did_call: bool,
    did_quiz: bool,
    share_link: str,
    lang: str,
    headline: str,
    intro_html: str,
) -> str:
    """Full HTML body. Every email reuses this skeleton so the user
    sees a consistent surface across signup, friend-joined, quiz-done,
    and call-done."""
    s = _strings(lang)
    # Mirror the /waitlist page's share semantics: WA / Viber buttons
    # share the user's *own* referral link with a friend (not chat
    # Peter). Same copy as ``doShare()`` in
    # product/web/public/waitlist/index.html so the email and the page
    # produce an identical pre-filled message.
    share_msg = f"{s['share_body']} {share_link}"
    wa_link = f"https://wa.me/?text={_url_encode(share_msg)}"
    viber_link = f"viber://forward?text={_url_encode(share_msg)}"
    # Deep link back to the user's own /waitlist position page. Each
    # climb card below also deep-links to its matching step on the SPA
    # so tapping any card in the email lands on the same screen the
    # equivalent button on the live page would open.
    token = str(row.get("id") or "")
    page_link = _position_link(token, "position")
    call_link = _position_link(token, "chat")
    quiz_link = _position_link(token, "q1")
    pos_str = f"#{position}" if position else "#?"
    behind_str = f"{behind:,}".replace(",", " ")
    invite_done = len(invited_names) >= 3

    avatars = _render_avatar_slots_html(invited_names)
    invite_row_done_label = s["invite_done"] if invite_done else ""
    invite_bump = (f"\u2713 {invite_row_done_label}") if invite_done else s["bump_25"]
    invite_bump_color = "#d8c057" if invite_done else "#d8c057"

    climb_rows = (
        # Invite is rendered as the dark hero card directly below;
        # then call + quiz are paper rows that link straight to the
        # matching step on the /waitlist SPA.
        _render_climb_row(
            label=s["call_title"],
            bump=s["bump_15"],
            done=did_call,
            done_label=s["call_done"],
            href=call_link,
        )
        + _render_climb_row(
            label=s["quiz_title"],
            bump=s["bump_5"],
            done=did_quiz,
            done_label=s["quiz_done"],
            # Mirror the page's gate (``if (state.did_quiz) return;``):
            # once the quiz is complete the row is decorative, not a link.
            href=None if did_quiz else quiz_link,
        )
    )

    return f"""<!doctype html>
<html lang="{s['lang']}">
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;padding:0;background:#f5f7f2;font-family:Helvetica,Arial,sans-serif;color:#0e1a14;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f5f7f2;">
    <tr><td align="center" style="padding:32px 14px;">
      <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;width:100%;">

        <!-- LOGO + EYEBROW -->
        <tr><td align="center" style="padding:0 0 22px;">
          <img src="{_email_logo_url()}" alt="vitrina" width="160" style="display:block;width:160px;height:auto;border:0;outline:none;" />
          <div style="margin-top:10px;font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;letter-spacing:0.20em;text-transform:uppercase;color:rgba(14,26,20,0.55);font-weight:700;">{_e(s['eyebrow'])}</div>
        </td></tr>

        <!-- HEADLINE + INTRO -->
        <tr><td style="padding:0 6px 18px;">
          <h1 style="font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:26px;line-height:1.15;letter-spacing:-0.02em;color:#0e1a14;margin:0 0 12px;text-align:center;">{_e(headline)}</h1>
          <div style="font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#1f2a23;text-align:center;">{intro_html}</div>
        </td></tr>

        <!-- POSITION CARD (paper) -->
        <tr><td style="padding:0 0 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#ffffff;border:1.5px solid #0e1a14;">
            <tr><td align="center" style="padding:24px 16px 22px;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;letter-spacing:0.20em;text-transform:uppercase;color:rgba(14,26,20,0.55);font-weight:700;margin-bottom:8px;">{_e(s['spot_lbl'])}</div>
              <div style="font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:88px;line-height:0.9;letter-spacing:-0.06em;color:#0e1a14;">{pos_str}</div>
              <div style="font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:rgba(31,42,35,0.78);margin-top:10px;"><strong style="color:#0e1a14;font-weight:800;">{behind_str}</strong> {_e(s['behind_suffix'])}</div>
            </td></tr>
          </table>
        </td></tr>

        <!-- CLIMB HEADING -->
        <tr><td align="center" style="padding:6px 0 12px;">
          <div style="font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:16px;letter-spacing:-0.005em;text-transform:uppercase;color:#0e1a14;">{_e(s['climb_heading'])}</div>
        </td></tr>

        <!-- INVITE CARD (dark) -->
        <tr><td style="padding:0 0 10px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0e1a14;border:1.5px solid #0e1a14;">
            <tr><td style="padding:22px 22px 20px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td style="font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:20px;line-height:1.15;letter-spacing:-0.02em;text-transform:uppercase;color:#ffffff;">{_e(s['invite_title'])}</td>
                  <td align="right" style="font-family:'JetBrains Mono',Menlo,monospace;font-weight:800;font-size:14px;color:{invite_bump_color};white-space:nowrap;vertical-align:top;">{_e(invite_bump)}</td>
                </tr>
              </table>
              <div style="font-family:Helvetica,Arial,sans-serif;font-size:13.5px;line-height:1.45;color:rgba(255,255,255,0.74);margin:8px 0 4px;">{_e(s['invite_sub'])}</div>
              <div style="text-align:center;">{avatars}</div>
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(255,255,255,0.62);margin-bottom:6px;">{_e(s['share_lbl'])}</div>
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:14px;background:#ffffff;border:1.5px solid #d8c057;padding:12px 14px;margin:0 0 12px;color:#0e1a14;word-break:break-all;">
                <a href="{_e(share_link)}" style="color:#0e1a14;text-decoration:none;">{_e(share_link)}</a>
              </div>
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                <tr>
                  <td width="50%" style="padding-right:5px;">
                    <a href="{_e(wa_link)}" style="display:block;text-align:center;padding:11px 8px;background:#25d366;color:#0e1a14;text-decoration:none;font-family:Helvetica,Arial,sans-serif;font-weight:800;font-size:12px;letter-spacing:0.06em;text-transform:uppercase;">{_e(s['wa'])}</a>
                  </td>
                  <td width="50%" style="padding-left:5px;">
                    <a href="{_e(viber_link)}" style="display:block;text-align:center;padding:11px 8px;background:#7360f2;color:#ffffff;text-decoration:none;font-family:Helvetica,Arial,sans-serif;font-weight:800;font-size:12px;letter-spacing:0.06em;text-transform:uppercase;">{_e(s['viber'])}</a>
                  </td>
                </tr>
              </table>
            </td></tr>
          </table>
        </td></tr>

        <!-- CALL + QUIZ ROWS -->
        <tr><td style="padding:0;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            {climb_rows}
          </table>
        </td></tr>

        <!-- OPEN-PAGE CTA: every climb card already deep-links into
             the matching step on the SPA; this is the catch-all anchor
             for users who'd rather just open the page and look around. -->
        <tr><td align="center" style="padding:14px 0 6px;">
          <a href="{_e(page_link)}" style="display:inline-block;padding:13px 22px;background:#0e1a14;color:#ffffff;text-decoration:none;font-family:Helvetica,Arial,sans-serif;font-weight:800;font-size:13px;letter-spacing:0.06em;text-transform:uppercase;border:1.5px solid #0e1a14;">{_e(s['open_page'])} →</a>
        </td></tr>

        <!-- FOOTER -->
        <tr><td style="padding:24px 0 8px;text-align:center;">
          <div style="font-family:Helvetica,Arial,sans-serif;font-size:13px;color:rgba(14,26,20,0.62);margin:0 0 4px;">{_e(s['sign'])}</div>
          <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:rgba(14,26,20,0.4);letter-spacing:.12em;text-transform:uppercase;">{_e(s['footer'])}</div>
        </td></tr>

        <!-- LEGAL / GDPR FOOTER. Three short paragraphs aligned with
             the privacy + terms modal on /drop-01-v13 so the two
             surfaces a regulator might compare (the public landing
             page and the transactional email) say the same thing
             about: (1) status — no payment, no contract; (2) data
             rights — GDPR Art. 13/17; (3) legal entity. Visually
             quiet (small, low-contrast) so the marketing surface
             above stays the focus, but explicit enough that nobody
             can claim we hid the disclosure. -->
        <tr><td style="padding:18px 6px 14px;border-top:1px solid rgba(14,26,20,0.12);">
          <div style="font-family:Helvetica,Arial,sans-serif;font-size:11.5px;line-height:1.55;color:rgba(14,26,20,0.55);text-align:left;">
            <p style="margin:0 0 8px;"><strong style="color:rgba(14,26,20,0.72);">{_e(s['legal_status'])}</strong></p>
            <p style="margin:0 0 8px;">{_e(s['legal_data'])} <a href="mailto:hello@vitrina.bg" style="color:rgba(14,26,20,0.72);text-decoration:underline;">hello@vitrina.bg</a>.</p>
            <p style="margin:0 0 8px;">{_e(s['legal_entity'])}</p>
            <p style="margin:0;"><a href="mailto:hello@vitrina.bg?subject={_url_encode(s['unsubscribe_subject'])}" style="color:rgba(14,26,20,0.72);text-decoration:underline;">{_e(s['unsubscribe'])}</a></p>
          </div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _render_branded_email_text(
    *,
    headline: str,
    intro_text: str,
    position: int,
    behind: int,
    share_link: str,
    invited_count: int,
    did_call: bool,
    did_quiz: bool,
    lang: str,
    token: str | None = None,
) -> str:
    s = _strings(lang)
    invite_state = (
        f"{s['invite_title']} ({invited_count}/3)"
        + (f" \u2713 {s['invite_done']}" if invited_count >= 3 else "")
    )
    page_link = _position_link(token, "position") if token else ""
    call_link = _position_link(token, "chat") if token else ""
    quiz_link = _position_link(token, "q1") if token else ""
    lines = [
        headline,
        "",
        intro_text,
        "",
        f"{s['spot_lbl']}: #{position}",
        f"{behind:,}".replace(",", " ") + f" {s['behind_suffix']}",
        "",
        s["climb_heading"] + ":",
        f"  {invite_state} {s['bump_25']}",
        f"    → {page_link}" if page_link else "",
        f"  {'✓ ' if did_call else ''}{s['call_title']} {s['bump_15']}",
        f"    → {call_link}" if call_link else "",
        f"  {'✓ ' if did_quiz else ''}{s['quiz_title']} {s['bump_5']}",
        f"    → {quiz_link}" if quiz_link and not did_quiz else "",
        "",
        f"{s['share_lbl']}: {share_link}",
    ]
    if page_link:
        lines += ["", f"{s['open_page']}: {page_link}"]
    lines += [
        "",
        s["sign"],
        "vitrina.bg",
        "",
        "—" * 8,
        s["legal_status"],
        "",
        f"{s['legal_data']} hello@vitrina.bg.",
        "",
        s["legal_entity"],
        "",
        f"{s['unsubscribe']}: mailto:hello@vitrina.bg?subject={_url_encode(s['unsubscribe_subject'])}",
    ]
    return "\n".join([line for line in lines if line is not None])


# ── 4 user-facing email triggers ────────────────────────────────────


def _send_signup_confirmation(*, row: dict[str, Any], position: int, share_link: str, lang: str) -> dict[str, Any]:
    """1/4 — Welcome email when a row is created. Mirrors the position
    page exactly so the user lands on /waitlist and sees what the email
    just told them."""
    if not row.get("email"):
        return {"ok": False, "skipped": "no_email"}
    sb = supabase_client.get_supabase()
    invited_names = _fetch_invited_first_names(sb, row.get("referral_code"))
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    behind = max(0, len(rows) - position)
    first_name = (row.get("first_name") or "").strip() or ("приятел" if (lang or "bg").startswith("bg") else "there")
    s = _strings(lang)
    if s["lang"] == "bg":
        subject = f"Vitrina · позиция #{position} в чакащата листа"
        headline = f"Здравей, {first_name}!"
        intro_html = (
            "Записан/а си в чакащата листа на Vitrina за София. Ето как се качваш по-нагоре:"
        )
        intro_text = "Записан/а си в чакащата листа на Vitrina за София."
    else:
        subject = f"Vitrina · waitlist position #{position}"
        headline = f"You're in, {first_name}."
        intro_html = "You're on the Sofia waitlist for Vitrina. Here's how to climb:"
        intro_text = "You're on the Sofia waitlist for Vitrina."

    html = _render_branded_email(
        row=row,
        position=position,
        behind=behind,
        invited_names=invited_names,
        did_call=bool(row.get("did_call")),
        did_quiz=bool(row.get("did_quiz")),
        share_link=share_link,
        lang=lang,
        headline=headline,
        intro_html=intro_html,
    )
    text = _render_branded_email_text(
        headline=headline,
        intro_text=intro_text,
        position=position,
        behind=behind,
        share_link=share_link,
        invited_count=len(invited_names),
        did_call=bool(row.get("did_call")),
        did_quiz=bool(row.get("did_quiz")),
        lang=lang,
        token=str(row.get("id") or ""),
    )
    return _resend_send(
        to=row["email"],
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "waitlist-welcome"}, {"name": "lang", "value": s["lang"]}],
    )


def _send_friend_joined(
    *,
    referrer_row: dict[str, Any],
    invitee_first_name: str,
    lang: str | None = None,
) -> dict[str, Any]:
    """2/4 — Sent to the *referrer* when one of their invitees signs
    up. Shows the updated 3-slot grid with the new initial filled in,
    and the bumped position number."""
    if not referrer_row.get("email"):
        return {"ok": False, "skipped": "no_email"}
    sb = supabase_client.get_supabase()
    rows = _rank_in_city(sb, referrer_row.get("city") or DEFAULT_CITY)
    pos = _position_of(rows, referrer_row["id"])
    behind = max(0, len(rows) - pos)
    invited_names = _fetch_invited_first_names(sb, referrer_row.get("referral_code"))
    use_lang = (lang or referrer_row.get("lang") or "bg")
    s = _strings(use_lang)
    first_name = (referrer_row.get("first_name") or "").strip() or ("приятел" if s["lang"] == "bg" else "there")
    invitee = (invitee_first_name or "").strip() or ("приятел" if s["lang"] == "bg" else "a friend")
    if s["lang"] == "bg":
        subject = f"{invitee} се записа от твоя линк · +25 места · #{pos}"
        headline = f"{invitee} се записа!"
        intro_html = (
            f"Качи се с <strong>+25 места</strong>. "
            f"Сега си на позиция <strong>#{pos}</strong>."
        )
        intro_text = (
            f"{invitee} се записа от твоя линк. Качи се с +25 места. Сега си на #{pos}."
        )
    else:
        subject = f"{invitee} just joined from your link · +25 spots · #{pos}"
        headline = f"{invitee} just joined!"
        intro_html = (
            f"You moved up <strong>+25 spots</strong>. "
            f"You're now at <strong>#{pos}</strong>."
        )
        intro_text = (
            f"{invitee} just joined from your link. You moved up +25 spots. You're now at #{pos}."
        )
    share_link = f"{_public_base_url()}/?r={referrer_row.get('referral_code') or ''}"
    html = _render_branded_email(
        row=referrer_row,
        position=pos,
        behind=behind,
        invited_names=invited_names,
        did_call=bool(referrer_row.get("did_call")),
        did_quiz=bool(referrer_row.get("did_quiz")),
        share_link=share_link,
        lang=use_lang,
        headline=headline,
        intro_html=intro_html,
    )
    text = _render_branded_email_text(
        headline=headline,
        intro_text=intro_text,
        position=pos,
        behind=behind,
        share_link=share_link,
        invited_count=len(invited_names),
        did_call=bool(referrer_row.get("did_call")),
        did_quiz=bool(referrer_row.get("did_quiz")),
        lang=use_lang,
        token=str(referrer_row.get("id") or ""),
    )
    return _resend_send(
        to=referrer_row["email"],
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "waitlist-friend-joined"}, {"name": "lang", "value": s["lang"]}],
    )


def _send_quiz_completed(*, row: dict[str, Any], lang: str | None = None) -> dict[str, Any]:
    """3/4 — Sent the moment ``did_quiz`` flips True. +5 spots."""
    if not row.get("email"):
        return {"ok": False, "skipped": "no_email"}
    sb = supabase_client.get_supabase()
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    pos = _position_of(rows, row["id"])
    behind = max(0, len(rows) - pos)
    invited_names = _fetch_invited_first_names(sb, row.get("referral_code"))
    use_lang = lang or row.get("lang") or "bg"
    s = _strings(use_lang)
    first_name = (row.get("first_name") or "").strip() or ("приятел" if s["lang"] == "bg" else "there")
    if s["lang"] == "bg":
        subject = f"+5 места · #{pos} · готов е тестът"
        headline = "Качи се с +5 места."
        intro_html = (
            f"Благодарим, {first_name} — попълни вкусовия тест и сега си на "
            f"<strong>#{pos}</strong>. Ще използваме отговорите ти, за да "
            f"подбираме това, което ти доставяме."
        )
        intro_text = (
            f"Благодарим, {first_name}. Попълни теста — качи се с +5 места. Сега си на #{pos}."
        )
    else:
        subject = f"+5 spots · #{pos} · taste quiz complete"
        headline = "You moved up +5 spots."
        intro_html = (
            f"Thanks, {first_name} — you finished the taste quiz, so you're "
            f"now at <strong>#{pos}</strong>. We'll use your answers to pick "
            f"what we send you."
        )
        intro_text = f"Thanks, {first_name}. You finished the quiz — +5 spots. You're now at #{pos}."

    share_link = f"{_public_base_url()}/?r={row.get('referral_code') or ''}"
    html = _render_branded_email(
        row=row,
        position=pos,
        behind=behind,
        invited_names=invited_names,
        did_call=bool(row.get("did_call")),
        did_quiz=True,
        share_link=share_link,
        lang=use_lang,
        headline=headline,
        intro_html=intro_html,
    )
    text = _render_branded_email_text(
        headline=headline,
        intro_text=intro_text,
        position=pos,
        behind=behind,
        share_link=share_link,
        invited_count=len(invited_names),
        did_call=bool(row.get("did_call")),
        did_quiz=True,
        lang=use_lang,
        token=str(row.get("id") or ""),
    )
    return _resend_send(
        to=row["email"],
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "waitlist-quiz-done"}, {"name": "lang", "value": s["lang"]}],
    )


def _send_call_completed(*, row: dict[str, Any], lang: str | None = None) -> dict[str, Any]:
    """4/4 — Sent when an admin marks ``did_call`` true on the founder
    dashboard (i.e. Peter actually had the WhatsApp / Viber chat).
    +15 spots."""
    if not row.get("email"):
        return {"ok": False, "skipped": "no_email"}
    sb = supabase_client.get_supabase()
    rows = _rank_in_city(sb, row.get("city") or DEFAULT_CITY)
    pos = _position_of(rows, row["id"])
    behind = max(0, len(rows) - pos)
    invited_names = _fetch_invited_first_names(sb, row.get("referral_code"))
    use_lang = lang or row.get("lang") or "bg"
    s = _strings(use_lang)
    first_name = (row.get("first_name") or "").strip() or ("приятел" if s["lang"] == "bg" else "there")
    if s["lang"] == "bg":
        subject = f"+15 места · #{pos} · след разговора с Питър"
        headline = "Качи се с +15 места."
        intro_html = (
            f"Благодаря за разговора, {first_name}. Сега си на "
            f"<strong>#{pos}</strong> в листата за София."
        )
        intro_text = f"Благодаря за разговора. +15 места. Сега си на #{pos}."
    else:
        subject = f"+15 spots · #{pos} · after our chat"
        headline = "You moved up +15 spots."
        intro_html = (
            f"Thanks for the chat, {first_name}. You're now at "
            f"<strong>#{pos}</strong> on the Sofia list."
        )
        intro_text = f"Thanks for the chat. +15 spots. You're now at #{pos}."

    share_link = f"{_public_base_url()}/?r={row.get('referral_code') or ''}"
    html = _render_branded_email(
        row=row,
        position=pos,
        behind=behind,
        invited_names=invited_names,
        did_call=True,
        did_quiz=bool(row.get("did_quiz")),
        share_link=share_link,
        lang=use_lang,
        headline=headline,
        intro_html=intro_html,
    )
    text = _render_branded_email_text(
        headline=headline,
        intro_text=intro_text,
        position=pos,
        behind=behind,
        share_link=share_link,
        invited_count=len(invited_names),
        did_call=True,
        did_quiz=bool(row.get("did_quiz")),
        lang=use_lang,
        token=str(row.get("id") or ""),
    )
    return _resend_send(
        to=row["email"],
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "waitlist-call-done"}, {"name": "lang", "value": s["lang"]}],
    )


def _notify_peter_new_signup(*, row: dict[str, Any], position: int) -> dict[str, Any]:
    nb = (row.get("home_neighbourhood") or "?").strip() or "?"
    name = (row.get("first_name") or "?").strip() or "?"
    phone = (row.get("phone") or "").strip()
    phone_digits = _clean_phone_digits(phone)
    wa_link = f"https://wa.me/{phone_digits}" if phone_digits else "(no phone)"
    viber_link = f"viber://chat?number=%2B{phone_digits}" if phone_digits else "(no phone)"
    subject = f"[Vitrina] New signup · #{position} · {nb} · {name}"
    text = (
        f"#{position} · {name} · {nb}\n"
        f"phone: {phone}\n"
        f"email: {row.get('email') or '—'}\n"
        f"home:  {row.get('home_address') or '—'}\n"
        f"work:  {row.get('work_company') or '—'} · {row.get('work_address') or '—'}\n"
        f"ref:   {row.get('referral_code') or '—'} (referred by {row.get('referred_by') or '—'})\n"
        f"wa:    {wa_link}\n"
        f"viber: {viber_link}\n"
        f"\nQuiz: {row.get('quiz_answers')}\n"
    )
    html = "<pre style=\"font-family:'JetBrains Mono',Menlo,monospace;font-size:13px;line-height:1.6\">" + _html_escape(text) + "</pre>"
    return _resend_send(
        to=_peter_admin_email(),
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "founder-new-signup"}],
    )


# Mirror the dashboard's dial-code dedupe so the WA / Viber tap-targets
# in the email actually route to a real number. Some signups have a
# doubled dial code stored on the row (e.g. "+359 359 88..." when the
# country picker contributed +359 AND the user typed 359 again at the
# start of the local number); without dedupe wa.me/359359... is dead.
_DOUBLED_DIAL_CODES = (
    "359", "40", "30", "39", "49", "34", "33", "44",
    "420", "421", "381", "389", "387", "382", "385",
)


def _clean_phone_digits(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    for dial in _DOUBLED_DIAL_CODES:
        if digits.startswith(dial + dial) and len(digits) > len(dial) * 2:
            return digits[len(dial):]
    return digits


def _notify_peter_call_requested(*, row: dict[str, Any], position: int, channel: str, lang: str) -> dict[str, Any]:
    """Telegram-style email to Peter when someone taps the founder-call
    CTA. The email is a triage ping — it should let Peter open WhatsApp
    OR Viber with a pre-typed opener in one tap from his phone, because
    that's the single moment where speed-to-reply matters most for the
    founder-call experience.

    Why both apps: roughly half of Sofia signups don't have WhatsApp
    (older demographic stayed on Viber), so the chat-options screen on
    the position page lets people pick. We mirror that here.

    Why a preloaded message: Viber's deeplink doesn't accept body text,
    only the number, so the WA button gets the opener via ``?text=`` and
    Viber gets just the number — Peter can paste from the email body.
    """
    name = (row.get("first_name") or "?").strip() or "?"
    phone = (row.get("phone") or "").strip()
    phone_digits = _clean_phone_digits(phone)
    use_lang = (lang or "bg").lower()
    if use_lang == "en":
        opener = (
            "Hi, I'm Peter from Vitrina. I saw you requested a call — "
            "when works for you, today or tomorrow?"
        )
    else:
        # Formal BG ("вие") — Peter introduces himself as Питър.
        opener = (
            "Здравейте, аз съм Питър от Vitrina. Видях, че поискахте да "
            "си поговорим — кога ще ви е удобно днес или утре?"
        )
    if phone_digits:
        wa_link = f"https://wa.me/{phone_digits}?text={_url_encode(opener)}"
        # viber://chat?number=+<digits> — the leading + must be URL-encoded
        # as %2B to survive plain-text email clients that auto-linkify.
        viber_link = f"viber://chat?number=%2B{phone_digits}"
    else:
        wa_link = "(no phone)"
        viber_link = "(no phone)"
    subject = f"[Vitrina] Call requested · {name} (#{position}) via {channel}"
    text = (
        f"#{position} · {name}\n"
        f"channel: {channel} (lang={use_lang})\n"
        f"phone: {phone}\n"
        f"email: {row.get('email') or '—'}\n"
        f"home:  {row.get('home_neighbourhood') or '—'} · {row.get('home_address') or '—'}\n"
        f"score: {_row_score(row)}\n"
        f"\n"
        f"WhatsApp (preloaded): {wa_link}\n"
        f"Viber (paste opener): {viber_link}\n"
        f"\n"
        f"Opener:\n{opener}\n"
        f"\n"
        f"dashboard: {_public_base_url()}/founder/waitlist\n"
    )
    # HTML version: dedicated tap-target buttons on top of the <pre> so
    # Peter can act in one tap on mobile without scrolling to find a
    # link buried in the body.
    btn_style = (
        "display:inline-block;padding:12px 18px;margin:0 8px 8px 0;"
        "font-family:Archivo,sans-serif;font-weight:800;font-size:13px;"
        "letter-spacing:0.10em;text-transform:uppercase;text-decoration:none;"
        "border-radius:2px"
    )
    if phone_digits:
        wa_btn = (
            f'<a href="{_html_escape(wa_link)}" '
            f'style="{btn_style};background:#25d366;color:#0e1a14">WhatsApp</a>'
        )
        viber_btn = (
            f'<a href="{_html_escape(viber_link)}" '
            f'style="{btn_style};background:#7360f2;color:#ffffff">Viber</a>'
        )
        buttons_html = f"<p style=\"margin:0 0 14px\">{wa_btn}{viber_btn}</p>"
    else:
        buttons_html = ""
    pre_html = (
        "<pre style=\"font-family:'JetBrains Mono',Menlo,monospace;"
        "font-size:13px;line-height:1.6\">" + _html_escape(text) + "</pre>"
    )
    html = buttons_html + pre_html
    return _resend_send(
        to=_peter_admin_email(),
        subject=subject,
        html=html,
        text=text,
        tags=[{"name": "kind", "value": "founder-call-requested"}, {"name": "channel", "value": channel}],
    )


def _url_encode(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
