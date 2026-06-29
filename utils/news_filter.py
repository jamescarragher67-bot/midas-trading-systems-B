"""
utils/news_filter.py - High-impact news event blackout filter

Blocks trade entries ±30 minutes around scheduled high-impact events.
Covers NFP, FOMC, CPI for the 2025-2026 trading period.

To extend: add entries to HIGH_IMPACT_EVENTS in (date_str, time_utc_str, description) format.
"""

from datetime import datetime, timezone, timedelta

BLACKOUT_MINUTES = 30

# (date YYYY-MM-DD, time HH:MM UTC, description)
HIGH_IMPACT_EVENTS = [
    # ── NFP (Non-Farm Payrolls) — first Friday of month, 08:30 ET ────────────
    # EST (UTC-5): Nov–Mar = 13:30 UTC  |  EDT (UTC-4): Apr–Oct = 12:30 UTC
    ("2025-10-03", "12:30", "NFP Oct 2025"),
    ("2025-11-07", "13:30", "NFP Nov 2025"),
    ("2025-12-05", "13:30", "NFP Dec 2025"),
    ("2026-01-09", "13:30", "NFP Jan 2026"),
    ("2026-02-06", "13:30", "NFP Feb 2026"),
    ("2026-03-06", "13:30", "NFP Mar 2026"),
    ("2026-04-03", "12:30", "NFP Apr 2026"),
    ("2026-05-01", "12:30", "NFP May 2026"),
    ("2026-06-05", "12:30", "NFP Jun 2026"),

    # ── FOMC rate decisions — press conference 14:30 ET ──────────────────────
    # EST: 19:30 UTC  |  EDT: 18:30 UTC
    ("2025-11-07", "19:30", "FOMC Nov 2025"),
    ("2025-12-17", "19:30", "FOMC Dec 2025"),
    ("2026-01-29", "19:30", "FOMC Jan 2026"),
    ("2026-03-19", "18:30", "FOMC Mar 2026"),
    ("2026-05-07", "18:30", "FOMC May 2026"),
    ("2026-06-18", "18:30", "FOMC Jun 2026"),

    # ── CPI — typically second week of month, 08:30 ET ───────────────────────
    ("2025-10-10", "12:30", "CPI Oct 2025"),
    ("2025-11-13", "13:30", "CPI Nov 2025"),
    ("2025-12-11", "13:30", "CPI Dec 2025"),
    ("2026-01-15", "13:30", "CPI Jan 2026"),
    ("2026-02-12", "13:30", "CPI Feb 2026"),
    ("2026-03-12", "13:30", "CPI Mar 2026"),
    ("2026-04-10", "12:30", "CPI Apr 2026"),
    ("2026-05-13", "12:30", "CPI May 2026"),
    ("2026-06-11", "12:30", "CPI Jun 2026"),
]

# Pre-parse to datetime objects for fast lookup
_PARSED: list[tuple[datetime, str]] = []
for _date_s, _time_s, _desc in HIGH_IMPACT_EVENTS:
    _dt_str = f"{_date_s} {_time_s}"
    _dt     = datetime.strptime(_dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    _PARSED.append((_dt, _desc))


def is_news_blackout(dt: datetime) -> bool:
    """
    Returns True if `dt` falls within BLACKOUT_MINUTES before or after
    any scheduled high-impact event.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    window = timedelta(minutes=BLACKOUT_MINUTES)
    for event_dt, _ in _PARSED:
        if abs(dt - event_dt) <= window:
            return True
    return False


def next_event(dt: datetime) -> tuple[datetime, str] | None:
    """Return the next upcoming event after `dt`, or None."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    upcoming = [(e, d) for e, d in _PARSED if e > dt]
    return min(upcoming, key=lambda x: x[0]) if upcoming else None
