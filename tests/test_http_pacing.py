from __future__ import annotations

from research_mirror_common.http_pacing import ConditionalCache, HumanePacer


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def test_pacer_enforces_min_interval_with_jitter():
    clock = _Clock()
    pacer = HumanePacer(
        min_interval_seconds=2.0, jitter_seconds=1.0,
        monotonic=clock.monotonic, sleeper=clock.sleep, rng=lambda: 0.5,
    )
    assert pacer.wait() == 0.0  # first request: no wait
    # Immediately ask again → must wait min_interval + jitter*0.5 = 2.5s
    slept = pacer.wait()
    assert slept == 2.5


def test_pacer_no_wait_when_enough_time_passed():
    clock = _Clock()
    pacer = HumanePacer(min_interval_seconds=2.0, jitter_seconds=0.0,
                        monotonic=clock.monotonic, sleeper=clock.sleep, rng=lambda: 0.0)
    pacer.wait()
    clock.t += 10.0  # plenty of time passes
    assert pacer.wait() == 0.0


def test_pacer_honours_retry_after():
    clock = _Clock()
    pacer = HumanePacer(min_interval_seconds=0.0, jitter_seconds=0.0,
                        monotonic=clock.monotonic, sleeper=clock.sleep, rng=lambda: 0.0)
    pacer.wait()
    pacer.note_retry_after(30.0)
    assert pacer.wait() == 30.0


def test_conditional_cache_emits_and_updates():
    cache = ConditionalCache()
    url = "https://example.test/feed"
    assert cache.headers(url) == {}  # nothing cached yet
    cache.update(url, {"ETag": '"abc"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"})
    h = cache.headers(url)
    assert h["If-None-Match"] == '"abc"'
    assert h["If-Modified-Since"] == "Wed, 21 Oct 2026 07:28:00 GMT"


def test_conditional_cache_not_modified():
    assert ConditionalCache.not_modified(304) is True
    assert ConditionalCache.not_modified(200) is False
