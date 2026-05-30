from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from research_mirror_common.health_integrity import (
    assess,
    humanize_age,
    max_age_minutes,
    staleness,
)

MSK = ZoneInfo("Europe/Moscow")


def test_fresh_payload_is_not_stale():
    now = datetime(2026, 5, 29, 19, 47, tzinfo=MSK)
    fresh = (now - timedelta(minutes=5)).isoformat()
    is_stale, minutes = staleness(fresh, now, max_age_minutes=60)
    assert is_stale is False
    assert minutes == 5


def test_old_payload_is_stale():
    now = datetime(2026, 5, 29, 19, 47, tzinfo=MSK)
    old = (now - timedelta(hours=6)).isoformat()
    is_stale, minutes = staleness(old, now, max_age_minutes=60)
    assert is_stale is True
    assert minutes == 360


def test_missing_timestamp_is_stale():
    now = datetime(2026, 5, 29, 19, 47, tzinfo=MSK)
    assert staleness(None, now, max_age_minutes=60) == (True, None)
    assert staleness("not-a-date", now, max_age_minutes=60) == (True, None)


def test_naive_and_utc_timestamps_compared_safely():
    now = datetime(2026, 5, 29, 16, 47, tzinfo=timezone.utc)  # 19:47 MSK
    # UTC payload 5 min ago, written as MSK-aware — must not look 3h stale.
    msk_recent = datetime(2026, 5, 29, 19, 42, tzinfo=MSK).isoformat()
    is_stale, minutes = staleness(msk_recent, now, max_age_minutes=60)
    assert is_stale is False
    assert minutes == 5


def test_per_source_thresholds():
    # d8 publishes daily → very tolerant; alenka scans often → tight.
    assert max_age_minutes("d8") >= 1000
    assert max_age_minutes("alenka") <= max_age_minutes("euler")
    assert max_age_minutes("unknown-bot") == 360  # default


def test_assess_returns_note_for_stale():
    now = datetime(2026, 5, 29, 19, 47, tzinfo=MSK)
    old = (now - timedelta(hours=6)).isoformat()
    is_stale, note = assess("euler", old, now)
    assert is_stale is True
    assert note == "стейл 6ч"


def test_d8_daily_cadence_not_flagged_at_a_few_hours():
    now = datetime(2026, 5, 29, 19, 47, tzinfo=MSK)
    three_hours = (now - timedelta(hours=3)).isoformat()
    assert assess("d8", three_hours, now) == (False, None)


def test_humanize_age():
    assert humanize_age(5) == "5м"
    assert humanize_age(360) == "6ч"
    assert humanize_age(None) == "?"
    assert humanize_age(60 * 24 * 3) == "3д"
