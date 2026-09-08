"""
utils/news_filter.py — Live high-impact economic event blackout filter.

Pulls the next 24h of events from the Finnhub economic calendar and
returns True if the current time is within ±NEWS_WINDOW_MINS of any
High-impact US event. Gold (XAU) is priced in USD, so US macro events
move gold — same filter covers both.

FAIL-CLOSED ON PURPOSE. If the API key is missing, the network errors,
Finnhub returns non-JSON, or the response shape is unrecognised, this
module returns (True, reason). A missed entry is recoverable; a trade
landed during NFP is not.

Schema note: Finnhub wraps its result as {"economicCalendar": [...]}.
Field names are `time` (not `date`), `event`, `country` (not `currency`),
and `impact` is lowercase ("high"). Filtering is on country=="US" since
Finnhub uses ISO country codes, not currencies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests.exceptions import RequestException

from config.settings import NEWS_WINDOW_MINS, FINNHUB_API_KEY
from utils.logger import setup_logger

logger = setup_logger("news_filter")

_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"

# XAU is priced in USD, so US high-impact events are the ones that move
# gold. Finnhub uses ISO country codes — "US" only.
_TARGET_COUNTRY = "US"

# Cache the calendar for 15 minutes — Finnhub's free tier is 60 calls/min
# and the bot polls every 60s, so caching keeps us well under quota and
# avoids hammering the API on every loop tick.
_CACHE_TTL_SECONDS = 900


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_events(from_dt: datetime, to_dt: datetime) -> list[dict[str, Any]] | None:
    """
    Call Finnhub for events in [from_dt, to_dt]. Returns the parsed list on
    success, or None on any failure (network, auth, schema, HTTP error).
    Callers must treat None as fail-closed.
    """
    params = {
        "from":  from_dt.strftime("%Y-%m-%d"),
        "to":    to_dt.strftime("%Y-%m-%d"),
        "token": FINNHUB_API_KEY,
    }
    try:
        resp = requests.get(_FINNHUB_URL, params=params, timeout=10)
    except RequestException as e:
        logger.error(f"News API: network error — {e}")
        return None

    if resp.status_code != 200:
        logger.error(f"News API: HTTP {resp.status_code} — {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.error("News API: response was not JSON")
        return None

    # Finnhub wraps the array: {"economicCalendar": [...]}
    if not isinstance(data, dict) or "economicCalendar" not in data:
        logger.error(f"News API: unexpected response shape — keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
        return None

    events = data["economicCalendar"]
    if not isinstance(events, list):
        logger.error(f"News API: economicCalendar is not a list — {type(events).__name__}")
        return None

    return events


def _filter_high_impact_us(events: list[dict[str, Any]]) -> list[tuple[datetime, str]]:
    """
    Pull only US high-impact events out of the raw response, parsing each
    `time` field into a UTC datetime. Items missing required fields are
    skipped (logged at debug) — they're treated as malformed, not as empty
    events, so a missing `impact` field never silently passes the filter.
    """
    out: list[tuple[datetime, str]] = []
    for ev in events:
        try:
            # Finnhub returns "high" lowercase; case-fold to be safe.
            if str(ev.get("impact", "")).lower() != "high":
                continue
            if ev.get("country") != _TARGET_COUNTRY:
                continue
            raw_time = ev["time"]
            event_dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            out.append((event_dt, ev.get("event", "Unknown Event")))
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"News API: skipped malformed event ({e})")
            continue
    return out


# Module-level cache — one calendar fetch every 15 minutes, shared across calls.
_cache: dict[str, Any] = {"events": None, "expires_at": None}


def _get_upcoming_events() -> list[tuple[datetime, str]] | None:
    """
    Return cached high-impact US events, refreshing the cache when stale.
    Returns None on any refresh failure (fail-closed signal).
    """
    now = _now_utc()
    if _cache["events"] is not None and _cache["expires_at"] > now:
        return _cache["events"]

    # 24h window covers ±window on either side with margin.
    raw = _fetch_events(now - timedelta(hours=2), now + timedelta(hours=24))
    if raw is None:
        return None

    filtered = _filter_high_impact_us(raw)
    _cache["events"] = filtered
    _cache["expires_at"] = now + timedelta(seconds=_CACHE_TTL_SECONDS)
    logger.info(f"News API: loaded {len(filtered)} high-impact US events "
                f"(cache TTL {_CACHE_TTL_SECONDS}s)")
    return filtered


def is_news_blackout(dt: datetime | None = None) -> tuple[bool, str]:
    """
    Returns (True, reason) if `dt` is within ±NEWS_WINDOW_MINS of any
    high-impact US event, else (False, "").

    FAIL-CLOSED: returns (True, reason) if the feed is unavailable. The
    `reason` string in the fail-closed case explains the failure so the
    caller can log it instead of a generic "blocked by news".
    """
    if dt is None:
        dt = _now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    events = _get_upcoming_events()
    if events is None:
        return True, "News feed unavailable (API error)"

    window = timedelta(minutes=NEWS_WINDOW_MINS)
    for event_dt, desc in events:
        if abs(dt - event_dt) <= window:
            return True, f"{desc} at {event_dt.strftime('%H:%M UTC')}"

    return False, ""
