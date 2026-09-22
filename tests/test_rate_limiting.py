"""Backoff and circuit-breaking against a throttled endpoint.

Google's free endpoint answers a throttled request with a bare 429 -- no
Retry-After, no RateLimit-* headers -- so there is no server-supplied delay to
honour and the client has to pace itself.
"""
import pytest

import run


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(run.time, "sleep", lambda *_: None)


class Boom(Exception):
    pass


class TooMany(Exception):
    def __str__(self):
        return "Server Error: You made too many requests to the server."


class FakeTranslator:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        outcome = self.results.pop(0) if self.results else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestIsRateLimited:
    @pytest.mark.parametrize("exc", [TooMany(), Exception("HTTP 429"),
                                     Exception("Too Many Requests")])
    def test_recognises_throttling(self, exc):
        assert run.is_rate_limited(exc) is True

    @pytest.mark.parametrize("exc", [Boom("connection reset"), Exception("timeout"),
                                     ValueError("bad input")])
    def test_ignores_ordinary_failures(self, exc):
        assert run.is_rate_limited(exc) is False

    def test_recognises_the_deep_translator_exception(self):
        if run.TooManyRequests is None:
            pytest.skip("deep-translator too old to expose TooManyRequests")
        assert run.is_rate_limited(run.TooManyRequests()) is True


class TestRateLimiter:
    def test_starts_unthrottled(self):
        limiter = run.RateLimiter()
        assert not limiter.throttled and not limiter.exhausted

    def test_delay_doubles_with_each_refusal(self):
        limiter = run.RateLimiter(base_delay=60, cap=10000)
        seen = []
        for _ in range(4):
            limiter.record_limit()
            seen.append(limiter.delay)
        assert seen == [60, 120, 240, 480]

    def test_delay_is_capped(self):
        limiter = run.RateLimiter(base_delay=60, cap=600)
        for _ in range(20):
            limiter.record_limit()
        assert limiter.delay == 600

    def test_success_resets_the_streak(self):
        limiter = run.RateLimiter(base_delay=60)
        limiter.record_limit()
        limiter.record_limit()
        limiter.record_success()
        assert not limiter.throttled
        limiter.record_limit()
        assert limiter.delay == 60

    def test_exhausts_after_the_configured_number(self):
        limiter = run.RateLimiter(give_up_after=3)
        for _ in range(2):
            limiter.record_limit()
        assert not limiter.exhausted
        limiter.record_limit()
        assert limiter.exhausted

    def test_wait_jitters_around_the_delay(self):
        limiter = run.RateLimiter(base_delay=100, cap=10000)
        limiter.record_limit()
        slept = []
        for _ in range(30):
            limiter.wait(sleeper=slept.append)
        assert all(80 <= s <= 120 for s in slept)
        assert len(set(slept)) > 1, "jitter should vary the delay"


class TestTranslateWithRetryUnderThrottling:
    def test_rate_limit_returns_immediately_without_retrying(self):
        """Retrying into an hour-long block wastes time and deepens it."""
        t = FakeTranslator([TooMany(), "ola"])
        limiter = run.RateLimiter()
        assert run.translate_with_retry(t, "hello", limiter=limiter) is None
        assert len(t.calls) == 1
        assert limiter.throttled

    def test_ordinary_errors_still_retry(self):
        t = FakeTranslator([Boom("blip"), "ola"])
        limiter = run.RateLimiter()
        assert run.translate_with_retry(t, "hello", limiter=limiter) == "ola"
        assert len(t.calls) == 2
        assert not limiter.throttled

    def test_success_clears_a_previous_streak(self):
        limiter = run.RateLimiter()
        limiter.record_limit()
        t = FakeTranslator(["ola"])
        run.translate_with_retry(t, "hello", limiter=limiter)
        assert not limiter.throttled

    def test_works_without_a_limiter(self):
        t = FakeTranslator([TooMany(), TooMany(), TooMany(), TooMany()])
        assert run.translate_with_retry(t, "hello") is None


class TestTranslateChunkUnderThrottling:
    def test_throttled_batch_skips_the_per_sentence_fallback(self):
        """40 individual requests would all be refused identically."""
        t = FakeTranslator([TooMany()])
        limiter = run.RateLimiter()
        assert run.translate_chunk(t, ["a", "b", "c"], limiter) == [None, None, None]
        assert len(t.calls) == 1

    def test_ordinary_batch_failure_still_falls_back(self):
        t = FakeTranslator([Boom()] * 4 + ["um", "dois"])
        limiter = run.RateLimiter()
        assert run.translate_chunk(t, ["one", "two"], limiter) == ["um", "dois"]

    def test_throttling_mid_fallback_abandons_the_rest(self):
        # Batch fails normally, first sentence succeeds, second is throttled.
        t = FakeTranslator([Boom()] * 4 + ["um", TooMany()])
        limiter = run.RateLimiter()
        result = run.translate_chunk(t, ["one", "two", "three"], limiter)
        assert result == ["um", None, None]
        assert "three" not in t.calls

    def test_still_returns_a_full_length_list(self):
        t = FakeTranslator([TooMany()])
        assert len(run.translate_chunk(t, ["a", "b", "c", "d"], run.RateLimiter())) == 4
