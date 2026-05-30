"""Silent-failure detection from already-collected state — no source requests.

A bot can report a green health payload while being silently broken: a frozen
health JSON (the payload stopped refreshing), a scan loop that died, or a scrape
that returns nothing while claiming OK. This module classifies a source's health
*payload freshness* using only the timestamp the bot already wrote, so the
StatusHealthAI summary (and an optional owner alert) can surface "looks green but
is stale" without polling any source site.

The health payload is rewritten on every scan cycle (even with zero new
materials), so its age tracks the scan cadence. Thresholds below are per-source
cadence × a generous factor.
"""

from __future__ import annotations

from datetime import datetime, timedelta


# Max payload age (minutes) before a "green" report is treated as stale.
# Generous thresholds: they catch multi-hour freezes (the euler.json class was
# ~6 h) without false-flagging normal low-activity gaps. Tighten later once
# real cadences are observed.
DEFAULT_MAX_AGE_MINUTES = 360
SOURCE_MAX_AGE_MINUTES: dict[str, int] = {
    "alenka": 180,     # scans ~10 min
    "alfa": 240,
    "mozgovik": 180,
    "euler": 300,      # scans 30–90 min
    "eulercal": 240,
    "rencap": 240,     # scans ~15 min, but low delivery activity
    "d8": 2160,        # publishes/scans ~daily (36 h)
    "banks": 2160,
    "mrsk": 2160,
}


def max_age_minutes(source_id: str) -> int:
    return SOURCE_MAX_AGE_MINUTES.get(source_id, DEFAULT_MAX_AGE_MINUTES)


def parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def staleness(updated_at, now: datetime, *, max_age_minutes: int) -> tuple[bool, int | None]:
    """Return (is_stale, age_minutes). A missing/unparseable timestamp is stale
    (age None). Comparison is tz-safe: naive timestamps are read as UTC."""
    dt = parse_dt(updated_at)
    if dt is None:
        return True, None
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        from datetime import timezone

        now = now.replace(tzinfo=timezone.utc)
    age = now - dt
    minutes = int(age.total_seconds() // 60)
    return age > timedelta(minutes=max_age_minutes), minutes


def humanize_age(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    if minutes < 60:
        return f"{minutes}м"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}ч"
    return f"{hours // 24}д"


def assess(source_id: str, updated_at, now: datetime) -> tuple[bool, str | None]:
    """High-level check for one source. Returns (is_stale, note)."""
    is_stale, minutes = staleness(updated_at, now, max_age_minutes=max_age_minutes(source_id))
    if not is_stale:
        return False, None
    return True, f"стейл {humanize_age(minutes)}"
