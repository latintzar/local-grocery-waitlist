"""Background curator for the waitlist community-options pool.

Why this module exists
----------------------
The waitlist quiz lets visitors write in their own answers for three
questions (``buy``, ``ripped``, ``wish``). Future visitors then see those
write-ins as suggestion chips below the presets. Without curation that
pool is messy: BG and EN entries get mixed, typos don't merge, and
offensive / off-topic submissions show up next to genuine ones.

This module runs after every quiz submit (via
``fastapi.BackgroundTasks``) and asks Claude Sonnet 4.6 (Anthropic API
direct, via ``ANTHROPIC_API_KEY``) to:

  1. **Merge** near-duplicates and typos into one canonical entry.
  2. **Translate** so each entry has both an EN and a BG label.
  3. **Drop** slurs / nonsense / clearly off-topic submissions, with a
     stated reason kept for audit.

The curated arrays are written to ``vitrina_waitlist_community_curated``
(see ``migrations/020_waitlist_community_curated.sql``) and the read
endpoint serves them by language. If the LLM call fails the previous
snapshot stays in place; if no snapshot has ever been written, the
read endpoint falls back to today's raw aggregation (cold start only).
The user-facing quiz never breaks because of a curator failure.

Why Anthropic API direct (not Bedrock / OpenRouter / packages.llm)
------------------------------------------------------------------
Peter's call. Vitrina's existing chat agent already goes through
``ANTHROPIC_API_KEY`` direct for prompt-caching reasons — this curator
follows the same billing path so spend lands in one account.
``packages.llm`` doesn't have an Anthropic-direct provider today; if a
second consumer needs this, the right move is to add an
``anthropic_direct`` provider there and switch this module over.
HTTP itself goes through ``core.http_client_sync`` (the shared httpx
wrapper) per Constitution rule #4.

Concurrency model
-----------------
``BackgroundTasks`` runs after the response is sent but in the same
worker. We don't want a burst of 50 sign-ups in 30 seconds to fire 50
LLM calls at the same qid, so each ``curate_qid`` call:

  - Acquires a per-qid lock (``threading.Lock``) so only one curation
    is in flight per qid at a time.
  - Reads ``last_curated_at`` from the curated table and skips if it
    ran less than ``_DEBOUNCE_SECONDS`` ago — bounding cost to ~3
    calls/min per qid worst-case across all workers.

For the "force re-curate" path used by the ops script, pass
``ignore_debounce=True``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from core import http_client_sync

_HERE = Path(__file__).resolve()
_MCP_DIR = _HERE.parent.parent / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

import supabase_client  # noqa: E402

logger = logging.getLogger(__name__)


# ── configuration ────────────────────────────────────────────────────

WAITLIST_TABLE = "vitrina_waitlist"
CURATED_TABLE = "vitrina_waitlist_community_curated"

_COMMUNITY_QIDS: tuple[str, ...] = ("buy", "ripped", "wish")

# Skip a curation if the last successful one ran less than this many
# seconds ago. Bounds LLM cost during sign-up bursts.
_DEBOUNCE_SECONDS = 60

# Raw input pre-filter — defence in depth so the LLM never sees junk.
_MAX_LABEL_LEN = 80
_MAX_INPUT_ITEMS = 400  # most popular N raw items go to the LLM
_URL_REGEX = re.compile(r"https?://|www\.", re.IGNORECASE)
_CONTROL_CHARS_REGEX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Bumped manually when the prompt changes shape. The value lands in the
# curated row so a future re-curation can decide to invalidate caches
# that used a stale prompt.
PROMPT_VERSION = 3

# Default model id. Anthropic API uses date-suffixed snapshots; flip
# this env var to pin a specific snapshot if needed (e.g. for a regression).
DEFAULT_MODEL = os.getenv("VITRINA_CURATOR_MODEL") or "claude-sonnet-4-6"
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"

# Question-text used in the LLM prompt so the model has the same
# context the visitor saw. EN+BG so the model can balance both.
_QUESTION_PROMPT: dict[str, dict[str, str]] = {
    "buy": {
        "en": "What do you actually want to be able to buy at a small corner shop near you?",
        "bg": "Какво всъщност искаш да можеш да купиш от малък магазин близо до теб?",
    },
    "ripped": {
        "en": "Have you ever felt ripped off shopping for food in Sofia?",
        "bg": "Случвало ли ти се е да преплатиш за храна в София?",
    },
    "wish": {
        "en": "What do you wish you could find but can't, anywhere in Sofia?",
        "bg": "Какво ти се иска да можеш да намериш, но не можеш — никъде в София?",
    },
}


# Per-qid lock so we don't run two curations for the same qid at once.
_qid_locks: dict[str, Lock] = {qid: Lock() for qid in _COMMUNITY_QIDS}


# ── helpers ──────────────────────────────────────────────────────────


def _aggregate_raw(sb: Any, qid: str) -> tuple[list[dict[str, Any]], int]:
    """Aggregate every raw write-in for ``qid`` from the waitlist.

    Returns ``(items, total_submission_count)`` where ``items`` is a
    list of ``{label, count}`` ordered by count desc, and
    ``total_submission_count`` is the number of waitlist rows that
    contributed at least one value (used as a cache-bust signal).
    """
    rows: list[dict[str, Any]] = (
        sb.table(WAITLIST_TABLE).select("quiz_answers").execute()
    ) or []
    counts: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    contributors = 0
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
        if not values:
            continue
        contributors += 1
        for v in values:
            v = (v or "").strip()
            if not v:
                continue
            if _URL_REGEX.search(v):
                continue
            v = _CONTROL_CHARS_REGEX.sub("", v)
            if not v:
                continue
            v = v[:_MAX_LABEL_LEN]
            key = v.casefold()
            entry = counts.get(key)
            if entry is None:
                counts[key] = {"label": v, "count": 1}
            else:
                entry["count"] += 1
    ranked = sorted(counts.values(), key=lambda e: (-e["count"], e["label"].casefold()))
    return ranked[:_MAX_INPUT_ITEMS], contributors


def _build_prompt(qid: str, items: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for the curator call."""
    q = _QUESTION_PROMPT.get(qid, {})
    q_en = q.get("en") or qid
    q_bg = q.get("bg") or qid

    system = (
        "You are curating free-text answers that visitors of a Bulgarian "
        "grocery waitlist (Vitrina, Sofia) wrote in to a multi-select "
        "quiz. Future visitors will see your output as ONE tight set of "
        "clickable suggestion chips in their language (Bulgarian or English).\n\n"
        "Your job is to produce a **small, high-quality, evolving shortlist** "
        "that reflects what the community actually said — not a dump of "
        "every rephrasing.\n\n"
        "Rules, in order of importance:\n"
        "  1. NEVER invent an answer that isn't grounded in the input. You "
        "     merge, translate, normalise wording, and drop — you do not "
        "     fabricate themes nobody wrote.\n"
        "  2. **Semantic dedup is mandatory.** If two lines mean the same "
        "     thing with different words (e.g. \"too expensive\" vs "
        "\"overpriced\", \"never\" vs \"no not really\", \"fresh veg\" vs "
        "\"good vegetables\"), they MUST become ONE chip with `count` = sum "
        "of merged rows and every original label listed in `merged_from`. "
        "Aim for **distinct meanings**, not distinct phrasings.\n"
        "  3. Translate: every surviving entry has BOTH `label_en` and "
        "`label_bg`. Natural everyday Sofia supermarket language, not formal.\n"
        "  4. Drop offensive, nonsense, spam URLs, clearly off-topic, or "
        "unrecoverably unclear lines — record in `dropped` with a short reason.\n"
        "  5. Cap each label at 45 characters — UI chips.\n"
        "  6. Output **at most 12 items** total. Prefer fewer strong chips "
        "over many redundant ones.\n"
        "  7. **Order `items` for scanability** (not raw count-only):\n"
        "     • For the ripped-off question (`ripped`): GROUP by answer "
        "       stance — (A) explicit Never / No / haven't felt that, "
        "       (B) Sometimes / mixed / rarely, "
        "       (C) Yes-style complaints (price, quality, checkout, delivery, "
        "       etc.). Within each group sort by count descending. "
        "       Never hide the clean negative answer in the middle of "
        "       yes-type complaints.\n"
        "     • For `buy` and `wish`: sort by count descending, but if counts "
        "       are close, put shorter, broader category labels before "
        "       niche duplicates.\n\n"
        "Output format: a single JSON object with keys `items` and `dropped` "
        "only. No prose, no markdown fences."
    )

    items_block = "\n".join(
        f"  - count={it['count']}, label={json.dumps(it['label'], ensure_ascii=False)}"
        for it in items
    ) or "  (none)"

    user = (
        f"Question (English): {q_en}\n"
        f"Question (Bulgarian): {q_bg}\n\n"
        f"Raw write-ins (already casefold-deduped, ordered by frequency):\n"
        f"{items_block}\n\n"
        "Return JSON shaped like:\n"
        "{\n"
        '  "items": [\n'
        '    {"label_en": "...", "label_bg": "...", "count": 12, "merged_from": ["...", "..."]},\n'
        "    ...\n"
        "  ],\n"
        '  "dropped": [\n'
        '    {"label": "...", "reason": "offensive|nonsense|off-topic|unclear|spam"},\n'
        "    ...\n"
        "  ]\n"
        "}"
    )
    if qid == "ripped":
        user += (
            "\n\nSPECIAL — this question is about feeling ripped off on *food shopping in Sofia*. "
            "Many inputs are long paragraphs or mixed languages. Every `label_en` and `label_bg` MUST be "
            "a SHORT chip a user can tap (max 45 characters). Summarise rants into one chip, e.g. "
            '"Yes — seasonal produce overpriced" / "Да — прекалено скъпи сезонни зеленчуци". '
            "Never output multi-sentence chip text. Merge obvious duplicates of yes/no/quality/price themes.\n"
            "Keep **one** canonical Never/No chip and **one** Sometimes-style chip if the data supports them; "
            "do not emit multiple chips that only differ by wording (e.g. \"No\" vs \"Never happened\" vs \"Not really\")."
        )
    return system, user


def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    api_key: str,
    timeout: float = 60.0,
) -> str:
    """Single Messages API call. Returns the assistant text content.

    Raises on non-2xx, timeout, or empty response. Caller catches and
    logs; we never let curator errors propagate to the user request.
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    url = f"{ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages"
    with http_client_sync(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"anthropic_http_{resp.status_code}: {resp.text[:300]}"
        )
    payload = resp.json()
    blocks = payload.get("content") or []
    text_parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    text = "".join(text_parts).strip()
    if not text:
        raise RuntimeError("anthropic_empty_response")
    return text


def _parse_curator_output(raw: str) -> dict[str, Any]:
    """Tolerant JSON parser — strips markdown fences if the model added them."""
    txt = raw.strip()
    if txt.startswith("```"):
        first_nl = txt.find("\n")
        if first_nl != -1:
            txt = txt[first_nl + 1 :]
        if txt.endswith("```"):
            txt = txt[:-3]
        txt = txt.strip()
    return json.loads(txt)


_WORD_TOKENS = re.compile(r"\w+", re.UNICODE)


def _semantic_bag_key(label_en: str) -> str:
    """Loose key so differently worded same-meaning chips collapse post-LLM."""
    words = _WORD_TOKENS.findall((label_en or "").casefold())
    if len(words) <= 1:
        return "|".join(words) if words else ""
    return " ".join(sorted(words))


def _dedupe_semantic_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge rows that share the same bag-of-words key (keeps richer labels)."""
    best: dict[str, dict[str, Any]] = {}
    order_keys: list[str] = []
    for it in items:
        key = _semantic_bag_key(it["label_en"])
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = {
                "label_en": it["label_en"],
                "label_bg": it["label_bg"],
                "count": it["count"],
                "merged_from": list(it.get("merged_from") or []),
            }
            order_keys.append(key)
            continue
        prev["count"] += it["count"]
        mf = list(prev.get("merged_from") or [])
        for x in it.get("merged_from") or []:
            if x not in mf:
                mf.append(x)
        prev["merged_from"] = mf
        # Prefer slightly longer label if still chip-sized (often clearer).
        if len(it["label_en"]) > len(prev["label_en"]) and len(it["label_en"]) <= 45:
            prev["label_en"] = it["label_en"]
            prev["label_bg"] = it["label_bg"]
    return [best[k] for k in order_keys if k in best]


_RIPPED_SOMETIMES_PAT = re.compile(
    r"\b(sometimes|rarely|occasionally|mixed|понякога|рядко|зависи)\b",
    re.IGNORECASE,
)


def _ripped_sort_bucket(it: dict[str, Any]) -> int:
    """Classify ripped chips so Never/No stays easy to find (without Yes–false positives)."""
    en = (it.get("label_en") or "").strip().casefold()
    bg = (it.get("label_bg") or "").strip().casefold()

    def looks_neg_start(s: str) -> bool:
        if not s:
            return False
        if s.startswith(
            (
                "no,",
                "no ",
                "never",
                "not really",
                "nah",
                "nope",
                "haven't",
                "havent",
                "не,",
                "не ",
                "никога",
                "няма ",
                "нищо такова",
            )
        ):
            return True
        # After an em dash: "— never …" / "— no …"
        return bool(
            re.search(r"[—\-]\s*(no|never|не)(?:\s|,|$)", s, re.IGNORECASE)
        )

    if looks_neg_start(en) or looks_neg_start(bg):
        return 0
    blob = f"{en} {bg}"
    if _RIPPED_SOMETIMES_PAT.search(blob):
        return 1
    return 2


def _order_items_for_qid(qid: str, items: list[dict[str, Any]]) -> None:
    """In-place sort: ripped uses polarity buckets; others by count."""
    if qid == "ripped":
        items.sort(
            key=lambda x: (
                _ripped_sort_bucket(x),
                -x["count"],
                x["label_en"].casefold(),
            )
        )
    else:
        items.sort(key=lambda x: (-x["count"], x["label_en"].casefold()))


def _normalise_curator_output(
    parsed: dict[str, Any],
    raw_items: list[dict[str, Any]],
    *,
    qid: str,
) -> dict[str, list[dict[str, Any]]]:
    """Validate + sanitise the curator's JSON. Drops malformed entries.

    Guarantees:
      - Each `item` has non-empty `label_en` and `label_bg` (≤45 chars).
      - `count` is a positive int (defaults to 1 if model omitted it).
      - `merged_from` is a list[str] (possibly empty).
      - Near-duplicate EN labels (bag-of-words) merged.
      - Order respects qid (polarity for ``ripped``).
      - At most 12 items.
    """
    raw_label_lookup = {it["label"].casefold(): it["label"] for it in raw_items}

    items_clean: list[dict[str, Any]] = []
    for it in (parsed.get("items") or [])[:60]:
        if not isinstance(it, dict):
            continue
        en = (it.get("label_en") or "").strip()[:45]
        bg = (it.get("label_bg") or "").strip()[:45]
        if not en or not bg:
            continue
        try:
            count = max(1, int(it.get("count") or 1))
        except (TypeError, ValueError):
            count = 1
        merged_raw = it.get("merged_from") or []
        merged: list[str] = []
        if isinstance(merged_raw, list):
            for m in merged_raw:
                if not isinstance(m, str):
                    continue
                m = m.strip()
                if not m:
                    continue
                merged.append(raw_label_lookup.get(m.casefold(), m)[:_MAX_LABEL_LEN])
        items_clean.append(
            {
                "label_en": en,
                "label_bg": bg,
                "count": count,
                "merged_from": merged,
            }
        )

    items_clean = _dedupe_semantic_items(items_clean)
    _order_items_for_qid(qid, items_clean)
    items_clean = items_clean[:12]

    dropped_clean: list[dict[str, str]] = []
    for d in (parsed.get("dropped") or [])[:80]:
        if not isinstance(d, dict):
            continue
        lbl = (d.get("label") or "").strip()[:_MAX_LABEL_LEN]
        reason = (d.get("reason") or "").strip()[:40]
        if lbl and reason:
            dropped_clean.append({"label": lbl, "reason": reason})

    return {"items": items_clean, "dropped": dropped_clean}


def _build_lang_arrays(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the curator items into two language-flat arrays.

    Frontend wants ``[{label, count, merged_from}]`` keyed by label,
    not the dual-language curator shape — so we split here.
    """
    items_en = [
        {
            "label": it["label_en"],
            "count": it["count"],
            "merged_from": it["merged_from"],
        }
        for it in items
    ]
    items_bg = [
        {
            "label": it["label_bg"],
            "count": it["count"],
            "merged_from": it["merged_from"],
        }
        for it in items
    ]
    return items_en, items_bg


# ── public surface ───────────────────────────────────────────────────


def curate_qid(qid: str, *, ignore_debounce: bool = False) -> dict[str, Any]:
    """Re-curate the community pool for one qid. Idempotent.

    Returns a small status dict for logs / the ops script. Never raises
    on LLM/HTTP/parse failures — it logs, marks the row ``last_run_ok=
    false``, leaves any prior good snapshot in place, and returns the
    failure status. The only exceptions that escape are programming
    errors (e.g. invalid qid).
    """
    if qid not in _COMMUNITY_QIDS:
        raise ValueError(f"unknown_qid: {qid!r}")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        msg = "anthropic_api_key_missing"
        logger.warning("curate_qid skipped: %s (qid=%s)", msg, qid)
        return {"qid": qid, "ok": False, "reason": msg, "skipped": True}

    lock = _qid_locks[qid]
    if not lock.acquire(blocking=False):
        return {"qid": qid, "ok": False, "reason": "in_flight", "skipped": True}

    try:
        sb = supabase_client.get_supabase()

        if not ignore_debounce:
            try:
                cur = (
                    sb.table(CURATED_TABLE)
                    .select("last_curated_at,last_run_ok")
                    .eq("qid", qid)
                    .limit(1)
                    .execute()
                ) or []
            except Exception:
                logger.exception("curate_qid: pre-fetch failed (qid=%s)", qid)
                cur = []
            if cur and cur[0].get("last_run_ok"):
                last_at = cur[0].get("last_curated_at")
                if last_at:
                    last_ts = _parse_pg_timestamp(last_at)
                    if last_ts is not None and (time.time() - last_ts) < _DEBOUNCE_SECONDS:
                        return {
                            "qid": qid,
                            "ok": True,
                            "reason": "debounced",
                            "skipped": True,
                        }

        items, contributors = _aggregate_raw(sb, qid)
        if not items:
            _upsert_curated(
                sb,
                qid,
                items_en=[],
                items_bg=[],
                dropped=[],
                contributors=contributors,
                model=DEFAULT_MODEL,
                ok=True,
                error=None,
            )
            return {"qid": qid, "ok": True, "items": 0, "contributors": 0}

        system_prompt, user_prompt = _build_prompt(qid, items)
        try:
            raw = _call_anthropic(
                system_prompt,
                user_prompt,
                model=DEFAULT_MODEL,
                api_key=api_key,
            )
            parsed = _parse_curator_output(raw)
            normalised = _normalise_curator_output(parsed, items, qid=qid)
        except Exception as exc:
            logger.exception("curate_qid llm/parse failed (qid=%s)", qid)
            _mark_failure(sb, qid, str(exc)[:300])
            return {"qid": qid, "ok": False, "reason": str(exc)[:120]}

        items_en, items_bg = _build_lang_arrays(normalised["items"])
        try:
            _upsert_curated(
                sb,
                qid,
                items_en=items_en,
                items_bg=items_bg,
                dropped=normalised["dropped"],
                contributors=contributors,
                model=DEFAULT_MODEL,
                ok=True,
                error=None,
            )
        except Exception as exc:
            logger.exception("curate_qid db write failed (qid=%s)", qid)
            return {"qid": qid, "ok": False, "reason": f"db_write: {exc!s}"[:160]}

        logger.info(
            "curate_qid done qid=%s in=%d out=%d dropped=%d contributors=%d",
            qid,
            len(items),
            len(normalised["items"]),
            len(normalised["dropped"]),
            contributors,
        )
        return {
            "qid": qid,
            "ok": True,
            "items": len(normalised["items"]),
            "dropped": len(normalised["dropped"]),
            "contributors": contributors,
        }
    finally:
        lock.release()


def schedule_curation_for_answers(
    background_tasks: Any, quiz_answers: dict[str, Any] | None
) -> None:
    """Hook for FastAPI route handlers. Schedule a curation per touched qid.

    Pass FastAPI's ``BackgroundTasks`` instance and the ``quiz_answers``
    dict the user just submitted. We schedule one task per qid that
    received a list-shaped value (the three community-input questions).
    Debounce inside ``curate_qid`` handles the burst case.

    ``background_tasks`` is the FastAPI parameter; we accept ``Any`` so
    the ops script can pass a no-op shim.
    """
    if os.getenv("VITRINA_GUEST") == "1": return
    if not quiz_answers or not isinstance(quiz_answers, dict):
        return
    seen: set[str] = set()
    for k, v in quiz_answers.items():
        qid = str(k).strip().lower()
        if qid not in _COMMUNITY_QIDS or qid in seen:
            continue
        if not isinstance(v, list) or not v:
            if not (isinstance(v, str) and v.strip()):
                continue
        seen.add(qid)
        try:
            background_tasks.add_task(curate_qid, qid)
        except Exception:
            logger.exception("schedule_curation_for_answers: add_task failed")


_STALE_REFRESH_SECONDS = float(os.getenv("VITRINA_CURATOR_STALE_SECONDS") or "300")


def curated_snapshot_age_seconds(qid: str) -> float | None:
    """Seconds since ``last_curated_at`` for this ``qid``, or ``None`` if no row."""
    if qid not in _COMMUNITY_QIDS:
        return None
    sb = supabase_client.get_supabase()
    try:
        rows = (
            sb.table(CURATED_TABLE)
            .select("last_curated_at")
            .eq("qid", qid)
            .limit(1)
            .execute()
        ) or []
    except Exception:
        logger.exception("curated_snapshot_age_seconds read failed qid=%s", qid)
        return None
    if not rows:
        return None
    ts = _parse_pg_timestamp(rows[0].get("last_curated_at"))
    if ts is None:
        return None
    return max(0.0, time.time() - ts)


def schedule_refresh_if_stale(background_tasks: Any, qid: str) -> None:
    """Re-run the curator in the background when the snapshot is missing or old.

    Quiz submit already triggers ``curate_qid``, but waitlist members who
    signed up earlier and open the quiz days later still need suggestions
    that reflect **today's** write-ins. A lightweight GET on
    ``community_answers`` schedules a refresh at most once per
    ``_STALE_REFRESH_SECONDS`` window per process (the debounce inside
    ``curate_qid`` still applies for rapid repeats).
    """
    if qid not in _COMMUNITY_QIDS:
        return
    if os.getenv("VITRINA_GUEST") == "1" or not os.getenv("ANTHROPIC_API_KEY"):
        return
    age = curated_snapshot_age_seconds(qid)
    if age is not None and age < _STALE_REFRESH_SECONDS:
        return
    try:
        background_tasks.add_task(curate_qid, qid)
    except Exception:
        logger.exception("schedule_refresh_if_stale add_task failed qid=%s", qid)


def get_curated(qid: str, lang: str) -> dict[str, Any] | None:
    """Read the curated row for ``qid``. ``None`` if no row exists yet.

    The route handler uses this and falls back to raw aggregation when
    we return ``None`` (cold start / pre-curator history).
    """
    if qid not in _COMMUNITY_QIDS:
        return None
    sb = supabase_client.get_supabase()
    try:
        rows = (
            sb.table(CURATED_TABLE)
            .select("items_en,items_bg,last_curated_at,last_run_ok")
            .eq("qid", qid)
            .limit(1)
            .execute()
        ) or []
    except Exception:
        logger.exception("get_curated: read failed (qid=%s)", qid)
        return None
    if not rows:
        return None
    row = rows[0]
    arr_key = "items_bg" if lang.lower().startswith("bg") else "items_en"
    items = row.get(arr_key) or []
    if not isinstance(items, list):
        items = []
    return {
        "qid": qid,
        "items": items,
        "last_curated_at": row.get("last_curated_at"),
        "last_run_ok": bool(row.get("last_run_ok")),
    }


# ── internals ────────────────────────────────────────────────────────


def _upsert_curated(
    sb: Any,
    qid: str,
    *,
    items_en: list[dict[str, Any]],
    items_bg: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
    contributors: int,
    model: str,
    ok: bool,
    error: str | None,
) -> None:
    payload = {
        "qid": qid,
        "items_en": items_en,
        "items_bg": items_bg,
        "dropped": dropped,
        "submission_count_at_curation": contributors,
        "model_used": model,
        "prompt_version": PROMPT_VERSION,
        "last_run_ok": ok,
        "last_error": error,
        "last_curated_at": _now_pg_iso(),
    }
    sb.table(CURATED_TABLE).upsert(payload, on_conflict="qid").execute()


def _mark_failure(sb: Any, qid: str, error: str) -> None:
    """Record the failure WITHOUT clobbering the previous good snapshot.

    We update only the metadata + flip ``last_run_ok=false``. The
    ``items_*`` columns retain whatever the last successful run wrote
    so the read endpoint keeps serving good data.
    """
    try:
        sb.table(CURATED_TABLE).upsert(
            {
                "qid": qid,
                "last_run_ok": False,
                "last_error": error,
                "last_curated_at": _now_pg_iso(),
                "model_used": DEFAULT_MODEL,
                "prompt_version": PROMPT_VERSION,
            },
            on_conflict="qid",
        ).execute()
    except Exception:
        logger.exception("_mark_failure: upsert failed (qid=%s)", qid)


def _now_pg_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _parse_pg_timestamp(value: Any) -> float | None:
    """Best-effort parse of a Postgres-emitted timestamptz to epoch seconds."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    from datetime import datetime

    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.timestamp()
