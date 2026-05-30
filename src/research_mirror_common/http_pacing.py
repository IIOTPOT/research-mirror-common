"""Request-efficiency helpers for source scrapers.

The source sites are rate-limited / anti-bot, so the goal is *fewer, more
human* requests — never more. Two tools:

- ``HumanePacer``: enforces a minimum interval between requests plus random
  jitter (so traffic isn't robotically periodic), and honours a server's
  ``Retry-After`` with backoff.
- ``ConditionalCache``: remembers each URL's ``ETag`` / ``Last-Modified`` and
  emits ``If-None-Match`` / ``If-Modified-Since`` headers so an unchanged
  listing returns ``304 Not Modified`` (no body, no downstream article fetches).

Both are dependency-injected (clock/sleeper/rng) so they're deterministic in
tests and add zero new requests by themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time as _time


class HumanePacer:
    def __init__(
        self,
        *,
        min_interval_seconds: float = 2.0,
        jitter_seconds: float = 1.0,
        monotonic: Callable[[], float] = _time.monotonic,
        sleeper: Callable[[float], None] = _time.sleep,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.jitter_seconds = max(0.0, float(jitter_seconds))
        self._monotonic = monotonic
        self._sleeper = sleeper
        if rng is None:
            import random

            rng = random.random
        self._rng = rng
        self._last_at: float | None = None
        self._backoff_until: float = 0.0

    def wait(self) -> float:
        """Sleep as needed before the next request. Returns seconds slept."""
        now = self._monotonic()
        target = now
        if self._last_at is not None:
            target = max(target, self._last_at + self.min_interval_seconds + self._rng() * self.jitter_seconds)
        target = max(target, self._backoff_until)
        sleep_for = max(0.0, target - now)
        if sleep_for > 0:
            self._sleeper(sleep_for)
        self._last_at = self._monotonic()
        return sleep_for

    def note_retry_after(self, seconds: float) -> None:
        """Server asked us to back off (HTTP 429 / Retry-After)."""
        self._backoff_until = max(self._backoff_until, self._monotonic() + max(0.0, float(seconds)))


class ConditionalCache:
    """Per-URL ETag / Last-Modified store for conditional GETs (→ 304, no body)."""

    def __init__(self) -> None:
        self._etag: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}

    def headers(self, url: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        if url in self._etag:
            headers["If-None-Match"] = self._etag[url]
        if url in self._last_modified:
            headers["If-Modified-Since"] = self._last_modified[url]
        return headers

    def update(self, url: str, response_headers: Mapping[str, str]) -> None:
        # Case-insensitive header lookup.
        lower = {str(k).lower(): v for k, v in dict(response_headers).items()}
        etag = lower.get("etag")
        last_modified = lower.get("last-modified")
        if etag:
            self._etag[url] = etag
        if last_modified:
            self._last_modified[url] = last_modified

    @staticmethod
    def not_modified(status_code: int) -> bool:
        return status_code == 304
