"""Translation batching, retry and per-sentence fallback.

time.sleep is patched out throughout: the retry path would otherwise spend
~14 seconds per exhausted call.
"""
import pytest

import run


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(run.time, "sleep", lambda *_: None)


class FakeTranslator:
    """Records calls and replays a scripted sequence of results.

    A scripted item may be a string (returned), an Exception (raised), or None
    (returned falsy, which the retry treats as failure).
    """

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        outcome = self.results.pop(0) if self.results else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestTranslateWithRetry:
    def test_returns_the_first_successful_result(self):
        t = FakeTranslator(["ola"])
        assert run.translate_with_retry(t, "hello") == "ola"
        assert len(t.calls) == 1

    def test_retries_after_a_transient_exception(self):
        """A rate-limit refusal is handled separately -- see test_rate_limiting.py."""
        t = FakeTranslator([RuntimeError("connection reset"), "ola"])
        assert run.translate_with_retry(t, "hello") == "ola"
        assert len(t.calls) == 2

    def test_retries_after_an_empty_result(self):
        t = FakeTranslator([None, "", "ola"])
        assert run.translate_with_retry(t, "hello") == "ola"
        assert len(t.calls) == 3

    def test_returns_none_once_tries_are_exhausted(self):
        t = FakeTranslator([RuntimeError("x")] * 4)
        assert run.translate_with_retry(t, "hello", tries=4) is None
        assert len(t.calls) == 4

    def test_honours_the_tries_argument(self):
        t = FakeTranslator([RuntimeError("x")] * 10)
        run.translate_with_retry(t, "hello", tries=2)
        assert len(t.calls) == 2

    def test_backoff_doubles_between_attempts(self, monkeypatch):
        delays = []
        monkeypatch.setattr(run.time, "sleep", lambda d: delays.append(d))
        t = FakeTranslator([RuntimeError("x")] * 4)
        run.translate_with_retry(t, "hello", tries=4, base_delay=2.0)
        # One sleep between each pair of attempts, never after the last.
        assert delays == [2.0, 4.0, 8.0]


class TestTranslateChunk:
    def test_fast_path_sends_one_joined_request(self):
        t = FakeTranslator(["um\ndois\ntres"])
        assert run.translate_chunk(t, ["one", "two", "three"]) == ["um", "dois", "tres"]
        assert t.calls == ["one\ntwo\nthree"]

    def test_line_count_mismatch_falls_back_to_per_sentence(self):
        """A dropped line would otherwise silently misalign every card."""
        t = FakeTranslator(["um\ndois", "um", "dois", "tres"])
        assert run.translate_chunk(t, ["one", "two", "three"]) == ["um", "dois", "tres"]
        assert t.calls[1:] == ["one", "two", "three"]

    def test_batch_failure_falls_back_to_per_sentence(self):
        t = FakeTranslator([RuntimeError("connection reset")] * 4 + ["um", "dois"])
        assert run.translate_chunk(t, ["one", "two"]) == ["um", "dois"]

    def test_one_bad_sentence_costs_only_itself(self):
        t = FakeTranslator(
            [RuntimeError("batch")] * 4      # batch attempt exhausts its retries
            + ["um"]                          # "one" succeeds
            + [RuntimeError("x")] * 3         # "two" exhausts its retries
            + ["tres"]                        # "three" succeeds
        )
        assert run.translate_chunk(t, ["one", "two", "three"]) == ["um", None, "tres"]

    def test_always_returns_a_list_the_length_of_the_chunk(self):
        t = FakeTranslator([RuntimeError("x")] * 50)
        assert len(run.translate_chunk(t, ["a", "b", "c"])) == 3

    def test_single_sentence_chunk(self):
        t = FakeTranslator(["ola"])
        assert run.translate_chunk(t, ["hello"]) == ["ola"]
