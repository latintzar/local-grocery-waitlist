"""Server-side proxy for Mapbox geocoding (used by the waitlist address picker).

Why this exists
---------------
The Vitrina waitlist (``apps/vitrina/product/web/public/waitlist/``) lets
people drop a pin on their home and work addresses. We started on
Nominatim (free OSM) but its Bulgaria coverage and rate-limit posture
caused real failed signups (3 user complaints on 2026-05-08, plus
$rageclick events in PostHog). Mapbox Search Box is much better, but
the free tier is hard-capped at 100,000 temporary-geocoding requests
per month — going over silently turns into actual money.

This module is the chokepoint that makes "going over" impossible:

1. **Token never reaches the browser.** ``MAPBOX_TOKEN`` is read from
   the server env. The waitlist HTML calls ``/api/geocode/forward`` and
   ``/api/geocode/reverse`` instead of Mapbox directly. There is no
   public token shipped on this code path that someone could scrape and
   spend our quota on.

2. **Atomic Postgres counter.** Every accepted request bumps a single
   row in ``vitrina_geocode_usage`` via ``vitrina_geocode_bump(month, cap)``
   (see migration 017). The function takes a row-level lock so two
   concurrent requests cannot both observe ``used = cap - 1`` and both
   decide to proceed — concurrency is serialised inside Postgres, the
   cap is honoured precisely.

3. **Default cap = 80,000.** That's a 20% safety margin below the 100k
   free tier. Override with ``VITRINA_GEOCODE_CAP_MONTHLY`` if you want
   something tighter (e.g. ``25000`` while we're still small).

4. **Process-local query cache.** A short LRU dedupes repeat searches
   (a user typing "vitosha 8" then deleting back to "vitosha" then
   retyping "vitosha 89" hits Mapbox once per unique prefix, not once
   per keystroke). Cache TTL is 30 minutes; resets on restart. This is
   just polish — the real cap is the SQL counter.

5. **Cap-breach semantics.** When the counter refuses, we respond
   ``503 {"capped": true}``. The waitlist client treats that as
   "Mapbox is unavailable, accept what the user typed as free text,
   let them proceed". Signup never blocks because of a cap — the worst
   case is we lose pin coordinates for the rest of the month.

For belt-and-braces safety, also set the *Mapbox account-level
spending limit* to your acceptable monthly cost (Mapbox dashboard →
Account → Plan & Billing → Usage Limit). Our SQL cap stops requests
*before* they reach Mapbox; the account limit is the second line of
defence if our counter is ever wrong (e.g. someone bypasses this
proxy).

Endpoints
---------

==========================================  ==============================
``POST /api/geocode/forward``               Search-as-you-type forward geocode
``POST /api/geocode/reverse``               Coord → human-readable label
``GET  /api/admin/geocode/usage``           Current month counter (debug)
==========================================  ==============================

Note on the prefix: every router in this service is mounted without
``/api/`` because Vercel rewrites strip ``/api/...`` to ``/...`` before
forwarding to the Hetzner backend (see ``apps/vitrina/vercel.json``).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel, Field

from core import http_client

_HERE = Path(__file__).resolve()
_MCP_DIR = _HERE.parents[2] / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

import supabase_client  # noqa: E402

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/geocode", tags=["geocode"])
admin_router = APIRouter(prefix="/admin/geocode", tags=["geocode", "admin"])


# ── tunables ────────────────────────────────────────────────────────

MAPBOX_FORWARD_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json"
MAPBOX_REVERSE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{lng},{lat}.json"

# Free-tier hard cap minus a 20% safety margin. Override per env if needed.
DEFAULT_CAP = 80_000

# Forward-search policy: scope to Bulgaria first; the waitlist is Sofia-
# launching but a few BG signups outside Sofia are fine and we still want
# them findable. Add countries here as launch widens.
DEFAULT_COUNTRIES = "bg"

# Bias forward searches toward Sofia centre (lng, lat). Mapbox accepts a
# `proximity` param that nudges results toward this point without
# excluding everything else.
SOFIA_PROXIMITY = "23.3219,42.6977"

# Cap autosuggest payload at 6 to match the existing UI list height.
DEFAULT_LIMIT = 6


# ── tiny LRU cache (process-local, 30 min TTL) ──────────────────────

_CACHE_TTL_SEC = 30 * 60
_CACHE_MAX = 1024
_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_cache_lock = Lock()


def _cache_get(key: str) -> Any | None:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if now - ts > _CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return payload


def _cache_put(key: str, payload: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), payload)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


# ── env helpers ─────────────────────────────────────────────────────


def _mapbox_token() -> str:
    tok = "" if os.getenv("VITRINA_GUEST") == "1" else (os.environ.get("MAPBOX_TOKEN") or "").strip()
    if not tok:
        raise HTTPException(
            status_code=503,
            detail="geocode_not_configured: MAPBOX_TOKEN env var is missing on the API host.",
        )
    return tok


def _cap() -> int:
    raw = os.environ.get("VITRINA_GEOCODE_CAP_MONTHLY", "").strip()
    if not raw:
        return DEFAULT_CAP
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        logger.warning(
            "VITRINA_GEOCODE_CAP_MONTHLY not an int: %r — using default %d", raw, DEFAULT_CAP
        )
        return DEFAULT_CAP


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── PostgREST RPC for the atomic bump ───────────────────────────────


async def _bump_counter(cap: int, month: str) -> int:
    """Call ``vitrina_geocode_bump`` via PostgREST RPC.

    Returns the new used count, or -1 if the cap is exhausted. Anything
    going wrong (DB unreachable etc.) is treated as "fail closed" — we
    return -1 so the request is refused. We never want a Postgres blip
    to silently let us spend money.
    """
    sb = supabase_client.get_supabase()
    url = f"{sb.url}/rest/v1/rpc/vitrina_geocode_bump"
    headers = sb._headers({"Prefer": "params=single-object"})
    body = {"p_month": month, "p_cap": cap}
    try:
        async with http_client(timeout=4.0) as client:
            resp = await client.post(url, headers=headers, json=body)
    except Exception as exc:  # noqa: BLE001
        logger.error("geocode_counter_unreachable: %s — failing closed", exc)
        return -1
    if resp.status_code >= 400:
        logger.error(
            "geocode_counter_error: %d %s — failing closed",
            resp.status_code,
            resp.text[:200],
        )
        return -1
    try:
        val = resp.json()
        if isinstance(val, list) and val:
            val = val[0]
        return int(val)
    except (ValueError, TypeError) as exc:
        logger.error(
            "geocode_counter_bad_payload: %s (%r) — failing closed", exc, resp.text[:200]
        )
        return -1


# ── request / response shapes ───────────────────────────────────────


class ForwardRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=200)
    lang: str = Field("bg", pattern=r"^(bg|en)$")
    countries: str | None = Field(
        None,
        max_length=64,
        description="Comma-separated ISO country codes. Defaults to 'bg'.",
    )


class ReverseRequest(BaseModel):
    lng: float
    lat: float
    lang: str = Field("bg", pattern=r"^(bg|en)$")


class Suggestion(BaseModel):
    id: str
    place_name: str
    text: str
    address: str | None = None
    neighbourhood: str | None = None
    place: str | None = None
    region: str | None = None
    country: str | None = None
    lat: float
    lng: float


class ForwardResponse(BaseModel):
    suggestions: list[Suggestion]
    capped: bool = False
    used_this_month: int | None = None


class ReverseResponse(BaseModel):
    label: str
    neighbourhood: str | None = None
    place: str | None = None
    capped: bool = False
    used_this_month: int | None = None


# ── Nominatim fallback (OSM has the Sofia neighbourhoods Mapbox lacks) ─


NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim usage policy requires a User-Agent identifying the app +
# a contact address so OSM can reach us if we abuse. Don't strip this.
NOMINATIM_UA = "Vitrina/1.0 (peter@vitrina.bg)"
# Neighbourhood polygons don't move — cache aggressively. 24h is
# conservative; could go longer but the LRU is process-local and
# resets on restart anyway.
_NOMINATIM_TTL_SEC = 24 * 60 * 60


async def _nominatim_neighbourhood(
    lat: float, lng: float, lang: str = "bg"
) -> str | None:
    """Resolve a coord to a Sofia neighbourhood via OSM/Nominatim.

    Why this exists: Mapbox geocoding/v5 returns empty
    neighborhood/locality contexts for every Sofia point we tested
    (2026-05-08 audit, n=30). OSM has the Sofia ж.к./кв./в.з. structure
    fully mapped — `address.suburb` gives us the canonical label
    ('ж.к. Иван Вазов', 'кв. Манастирски ливади', 'в.з. Драгалевци').
    This is the second tier in the geocoding stack: Mapbox stays
    primary for autocomplete + the address label; Nominatim fills in
    the neighbourhood after the fact.

    Fails open: any error returns None and the caller proceeds without
    enrichment. Nominatim's free tier has a 1 req/sec hard limit which
    we never approach (one call per signup or per reverse, cached 24h).
    """
    # Sofia neighbourhoods have one canonical name (Cyrillic). Forcing
    # accept-language=bg for the OSM call avoids splitting buckets like
    # "Център" vs "Centre" in the leaderboard. The ``lang`` arg is
    # accepted for API compatibility but ignored here.
    del lang  # noqa: F841 — explicitly unused
    key = f"nom|bg|{lat:.5f},{lng:.5f}"
    cached = _cache_get(key)
    if cached is not None:
        return cached or None
    params = {
        "format": "json",
        "lat": f"{lat}",
        "lon": f"{lng}",
        "addressdetails": "1",
        "accept-language": "bg",
        "zoom": "16",
    }
    try:
        async with http_client(timeout=4.0) as client:
            resp = await client.get(
                NOMINATIM_REVERSE_URL,
                params=params,
                headers={"User-Agent": NOMINATIM_UA},
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("nominatim_reverse_unreachable: %s", exc)
        return None
    if resp.status_code >= 400:
        logger.info("nominatim_reverse_status: %d", resp.status_code)
        return None
    try:
        addr = (resp.json() or {}).get("address", {}) or {}
    except Exception:  # noqa: BLE001
        return None
    nb = (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("quarter")
        or addr.get("village")
        or addr.get("city_district")
    )
    nb = (nb or "").strip() or None
    _cache_put(key, nb or "")
    return nb


# ── Mapbox feature → our flat shape ─────────────────────────────────


def _ctx_value(feature: dict[str, Any], key_prefix: str) -> str | None:
    for ctx in feature.get("context", []) or []:
        cid = ctx.get("id", "")
        if cid.startswith(key_prefix + "."):
            return ctx.get("text")
    return None


def _flatten(feature: dict[str, Any]) -> Suggestion | None:
    center = feature.get("center") or feature.get("geometry", {}).get("coordinates")
    if not center or len(center) < 2:
        return None
    place_types = feature.get("place_type") or []
    primary = (feature.get("text") or "").strip()
    address = None
    if "address" in place_types and feature.get("address"):
        address = f"{feature['address']} {primary}".strip()
    return Suggestion(
        id=str(feature.get("id") or ""),
        place_name=feature.get("place_name") or primary,
        text=primary,
        address=address or (primary if "address" in place_types else None),
        neighbourhood=_ctx_value(feature, "neighborhood")
        or _ctx_value(feature, "locality"),
        place=_ctx_value(feature, "place"),
        region=_ctx_value(feature, "region"),
        country=_ctx_value(feature, "country"),
        lat=float(center[1]),
        lng=float(center[0]),
    )


# ── routes ──────────────────────────────────────────────────────────


@router.post("/forward", response_model=ForwardResponse)
async def forward(req: ForwardRequest = Body(...)) -> ForwardResponse:
    """Search-as-you-type forward geocode through Mapbox.

    Bumps the monthly counter exactly once per Mapbox call. Cache hits
    do NOT bump (they don't hit Mapbox). When the cap is exhausted the
    response is 503 ``{"capped": true}`` and the client falls back to
    free-text submission.
    """
    if os.getenv("VITRINA_GUEST") == "1":
        raise HTTPException(status_code=503, detail="Geocoding requires your own provider")
    q = req.q.strip()
    if len(q) < 3:
        return ForwardResponse(suggestions=[])

    countries = (req.countries or DEFAULT_COUNTRIES).strip().lower()
    cache_key = f"f|{req.lang}|{countries}|{q.lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return ForwardResponse(suggestions=cached)

    cap = _cap()
    used = await _bump_counter(cap=cap, month=_month_key())
    if used < 0:
        logger.warning("geocode_capped: month=%s cap=%d", _month_key(), cap)
        raise HTTPException(
            status_code=503,
            detail={"capped": True, "message": "geocode_monthly_cap_reached"},
        )

    token = _mapbox_token()
    params = {
        "access_token": token,
        "language": req.lang,
        "limit": DEFAULT_LIMIT,
        "country": countries,
        "proximity": SOFIA_PROXIMITY,
        "types": "address,place,locality,neighborhood,postcode,district",
        "autocomplete": "true",
    }
    try:
        async with http_client(timeout=6.0) as client:
            resp = await client.get(MAPBOX_FORWARD_URL.format(q=q), params=params)
    except Exception as exc:  # noqa: BLE001
        logger.error("mapbox_forward_unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="geocode_upstream_unreachable") from exc
    if resp.status_code >= 400:
        logger.error("mapbox_forward_error: %d %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="geocode_upstream_error")

    feats = resp.json().get("features", []) or []
    suggestions = [s for s in (_flatten(f) for f in feats) if s is not None]
    _cache_put(cache_key, [s.model_dump() for s in suggestions])
    return ForwardResponse(suggestions=suggestions, used_this_month=used)


@router.post("/reverse", response_model=ReverseResponse)
async def reverse(req: ReverseRequest = Body(...)) -> ReverseResponse:
    """Reverse-geocode a coord into a human-readable address label.

    Used by the geolocation crosshair flow on the waitlist. Same cap
    semantics as forward — fails closed when capped, the client then
    accepts the typed text as-is.
    """
    cache_key = f"r|{req.lang}|{req.lng:.5f},{req.lat:.5f}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return ReverseResponse(**cached)

    cap = _cap()
    used = await _bump_counter(cap=cap, month=_month_key())
    if used < 0:
        logger.warning("geocode_capped: month=%s cap=%d", _month_key(), cap)
        raise HTTPException(
            status_code=503,
            detail={"capped": True, "message": "geocode_monthly_cap_reached"},
        )

    token = _mapbox_token()
    # NOTE: Mapbox geocoding/v5 reverse rejects `limit > 1` unless paired
    # with a *single* type. We previously sent `limit=5` with multiple
    # types and got a silent 422 on every "current location" tap. Since
    # we use the multi-type pick logic below, we want all types — so
    # drop `limit` entirely (default 1 per type).
    params = {
        "access_token": token,
        "language": req.lang,
        "types": "address,neighborhood,locality,place,region",
    }
    try:
        async with http_client(timeout=6.0) as client:
            resp = await client.get(
                MAPBOX_REVERSE_URL.format(lng=req.lng, lat=req.lat),
                params=params,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("mapbox_reverse_unreachable: %s", exc)
        raise HTTPException(status_code=502, detail="geocode_upstream_unreachable") from exc
    if resp.status_code >= 400:
        logger.error("mapbox_reverse_error: %d %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="geocode_upstream_error")

    feats = resp.json().get("features", []) or []
    if not feats:
        payload = {"label": "", "neighbourhood": None, "place": None}
        _cache_put(cache_key, payload)
        return ReverseResponse(used_this_month=used, **payload)

    by_type: dict[str, dict[str, Any]] = {}
    for f in feats:
        for t in f.get("place_type") or []:
            by_type.setdefault(t, f)
    pick = (
        by_type.get("address")
        or by_type.get("neighborhood")
        or by_type.get("locality")
        or by_type.get("place")
        or by_type.get("region")
        or feats[0]
    )
    primary = (pick.get("text") or "").strip()
    house_no = pick.get("address")
    if "address" in (pick.get("place_type") or []) and house_no:
        # BG: "Витоша 10"; EN: "10 Vitosha".
        if req.lang == "bg":
            label = f"{primary} {house_no}".strip()
        else:
            label = f"{house_no} {primary}".strip()
    else:
        label = primary or (pick.get("place_name") or "").split(",")[0].strip()

    neighbourhood = _ctx_value(pick, "neighborhood") or _ctx_value(pick, "locality")
    place = _ctx_value(pick, "place")
    # Mapbox geocoding/v5 returns NO neighbourhood for Sofia (verified
    # 2026-05-08 — every probe across 30 Sofia points came back with
    # empty neighborhood/locality contexts). Fall back to OSM/Nominatim
    # which has Sofia's ж.к./кв./в.з. structure properly mapped via
    # `address.suburb`. Single call, cached 24h, fails open.
    if not neighbourhood:
        nb_osm = await _nominatim_neighbourhood(req.lat, req.lng, req.lang)
        if nb_osm:
            neighbourhood = nb_osm
    payload = {"label": label, "neighbourhood": neighbourhood, "place": place}
    _cache_put(cache_key, payload)
    return ReverseResponse(used_this_month=used, **payload)


# ── admin / debug ───────────────────────────────────────────────────


def _admin_token_ok(authorization: str | None) -> bool:
    expected = (
        os.environ.get("VITRINA_ADMIN_TOKEN")
        or os.environ.get("LEO_ADMIN_TOKEN")
        or ""
    ).strip()
    if not expected or not authorization:
        return False
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return parts[1].strip() == expected


@admin_router.get("/usage")
async def usage(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Show the current-month counter. Auth: ``Authorization: Bearer <admin token>``."""
    if not _admin_token_ok(authorization):
        raise HTTPException(status_code=401, detail="unauthorized")
    sb = supabase_client.get_supabase()
    month = _month_key()
    cap = _cap()
    rows = (
        sb.table("vitrina_geocode_usage")
        .select("month_key,used,cap_when_seen,updated_at")
        .eq("month_key", month)
        .limit(1)
        .execute()
        or []
    )
    row = rows[0] if rows else {
        "month_key": month,
        "used": 0,
        "cap_when_seen": cap,
        "updated_at": None,
    }
    return {
        "month": month,
        "used": int(row.get("used") or 0),
        "cap": cap,
        "remaining": max(0, cap - int(row.get("used") or 0)),
        "updated_at": row.get("updated_at"),
    }
