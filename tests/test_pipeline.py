"""End-to-end create_anki_deck runs with the network stubbed out."""
import csv
import json
import os
import shutil

import pytest

import run

BOOK_HTML = """
<p>THE WAY OF DISCIPLINE</p>
<p>People look for the shortcut.</p>
<p>And if you came here looking for that:</p>
<p>You won't find it.</p>
<p>You won't find it.</p>
"""


# Stand-in "translations" that are real Portuguese, so the spacy pass finds
# content words and the vocabulary/lemma path is actually exercised.
PT_STUB = {"Hello there.": "O gato dorme na casa."}


@pytest.fixture(autouse=True)
def stub_network(monkeypatch, pt_nlp):
    """Replace the translator and TTS with deterministic local stand-ins."""

    class StubTranslator:
        def __init__(self, source, target):
            self.source, self.target = source, target

        def translate(self, text):
            lines = text.split("\n")
            if self.target == "pt":
                return "\n".join(PT_STUB.get(l, f"<pt>{l}") for l in lines)
            return "\n".join(f"<en>{l}" for l in lines)

    def stub_audio(text, filepath, provider, lang_config):
        with open(filepath, "wb") as f:
            f.write(b"ID3stub")

    monkeypatch.setattr(run, "GoogleTranslator", StubTranslator)
    monkeypatch.setattr(run, "generate_audio", stub_audio)
    monkeypatch.setattr(run.time, "sleep", lambda *_: None)


def read_tsv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="\t"))


class TestBookRun:
    @pytest.fixture
    def book(self, make_epub):
        return make_epub([("chapter1.xhtml", BOOK_HTML)])

    def test_writes_a_two_column_deck(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True)
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert rows and all(len(r) == 2 for r in rows)

    def test_english_on_the_front_generated_portuguese_on_the_back(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True)
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        front, back = rows[1]
        assert front == "People look for the shortcut."
        assert back.startswith("<pt>People look for the shortcut.")
        assert "[sound:" in back and "[sound:" not in front

    def test_audio_is_hashed_and_deduplicated(self, book):
        """The fixture repeats a line; both cards should share one clip."""
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True)
        base = os.path.dirname(book)
        rows = read_tsv(os.path.join(base, "deck_AnkiDeck.tsv"))
        clips = os.listdir(os.path.join(base, "deck_Audio"))
        assert len(rows) == 5
        assert len(clips) == 4
        assert rows[3][1] == rows[4][1]

    def test_output_name_defaults_to_a_slug(self, make_epub):
        book = make_epub([("chapter1.xhtml", "<p>Hello there.</p>")],
                         name="A Very Long Book Title -- 2017.epub")
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english", no_cache=True)
        assert os.path.exists(
            os.path.join(os.path.dirname(book), "a-very-long-book-title-2017_AnkiDeck.tsv")
        )

    def test_limit_zero_is_a_parse_only_dry_run(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", limit=0, no_cache=True)
        assert not os.path.exists(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))

    def test_limit_caps_the_number_of_cards(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", limit=2, no_cache=True)
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert len(rows) == 2


class TestCaches:
    @pytest.fixture
    def book(self, make_epub):
        return make_epub([("chapter1.xhtml", "<p>Hello there.</p>")])

    def test_translation_and_lemma_caches_are_written(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck")
        base = os.path.dirname(book)
        with open(os.path.join(base, "deck_translations.json"), encoding="utf-8") as f:
            assert json.load(f)["Hello there."] == "O gato dorme na casa."
        assert os.path.exists(os.path.join(base, "deck_lemmas.json"))

    def test_a_second_run_reuses_the_cache_instead_of_translating(self, book, monkeypatch):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck")

        def explode(*_a, **_k):
            raise AssertionError("translated despite a warm cache")

        monkeypatch.setattr(run, "translate_chunk", explode)
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck")

    def test_no_cache_writes_nothing(self, book):
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True)
        base = os.path.dirname(book)
        assert not os.path.exists(os.path.join(base, "deck_translations.json"))
        assert not os.path.exists(os.path.join(base, "deck_lemmas.json"))


class TestSubtitleRun:
    @pytest.fixture
    def srt(self, tmp_path):
        path = tmp_path / "ep.srt"
        path.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nO gato dorme.\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n[explosao distante]\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nA casa e azul.\n",
            encoding="utf-8",
        )
        return str(path)

    def test_study_language_on_the_front_by_default(self, srt):
        run.create_anki_deck(srt, "gtts", translator_name="google", no_cache=True)
        rows = read_tsv(srt.replace(".srt", "_AnkiDeck.tsv"))
        assert rows[0][0].startswith("O gato dorme. [sound:")
        assert rows[0][1].startswith("<en>O gato dorme.")

    def test_annotation_only_blocks_are_dropped(self, srt):
        run.create_anki_deck(srt, "gtts", translator_name="google", no_cache=True)
        rows = read_tsv(srt.replace(".srt", "_AnkiDeck.tsv"))
        assert len(rows) == 2

    def test_audio_is_numbered_by_position_not_hashed(self, srt):
        run.create_anki_deck(srt, "gtts", translator_name="google", no_cache=True)
        clips = sorted(os.listdir(srt.replace(".srt", "_Audio")))
        assert clips == ["ep_0001.mp3", "ep_0002.mp3"]

    def test_existing_clips_are_not_regenerated(self, srt, monkeypatch):
        run.create_anki_deck(srt, "gtts", translator_name="google", no_cache=True)

        def explode(*_a, **_k):
            raise AssertionError("regenerated an existing clip")

        monkeypatch.setattr(run, "generate_audio", explode)
        run.create_anki_deck(srt, "gtts", translator_name="google", no_cache=True)


class TooMany(Exception):
    def __str__(self):
        return "Server Error: You made too many requests to the server."


class TestThrottling:
    """Being throttled on glosses is survivable; on sentences it is not."""

    @pytest.fixture
    def book(self, make_epub):
        return make_epub([("chapter1.xhtml",
                           "<p>Hello there.</p><p>Hello again.</p><p>Goodbye now.</p>")])

    def test_gloss_throttling_keeps_every_card(self, book, monkeypatch):
        """Definitions are garnish -- losing them must not cost cards."""

        class Stub:
            def __init__(self, source, target):
                self.source, self.target = source, target

            def translate(self, text):
                if self.target == "en":       # the lemma/definition direction
                    raise TooMany()
                return "\n".join(f"<pt>{l}" for l in text.split("\n"))

        monkeypatch.setattr(run, "GoogleTranslator", Stub)
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True)
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert len(rows) == 3
        assert all(r[0] for r in rows)

    def test_sentence_throttling_stops_the_run(self, book, monkeypatch):
        class Stub:
            def __init__(self, source, target):
                self.source, self.target = source, target

            def translate(self, text):
                raise TooMany()

        monkeypatch.setattr(run, "GoogleTranslator", Stub)
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True,
                             rate_limit_give_up=1)
        deck = os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv")
        # Nothing translated, so nothing to write.
        assert not os.path.exists(deck) or read_tsv(deck) == []

    def test_a_partial_run_does_not_clobber_a_finished_deck(self, book, monkeypatch):
        """The caches carry progress forward; destroying finished work would not."""
        deck = os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("front\tback\n" * 50)

        class Stub:
            def __init__(self, source, target):
                self.source, self.target = source, target

            def translate(self, text):
                raise TooMany()

        monkeypatch.setattr(run, "GoogleTranslator", Stub)
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True,
                             rate_limit_give_up=1)
        assert len(read_tsv(deck)) == 50


class TestThrottledRetry:
    """The backoff must retry the SAME batch, not merely pause before the next one.

    A single-batch run (a small --limit) has no "next batch", so a version that
    only waits between batches never waits at all.
    """

    @pytest.fixture
    def book(self, make_epub):
        return make_epub([("chapter1.xhtml", "<p>Hello there.</p><p>Hello again.</p>")])

    @pytest.fixture
    def waits(self, monkeypatch):
        recorded = []
        original = run.RateLimiter.wait

        def spy(self, sleeper=None):
            recorded.append(self.delay)
            return original(self, sleeper=lambda _d: None)

        monkeypatch.setattr(run.RateLimiter, "wait", spy)
        return recorded

    def _flaky(self, failures, refused_direction):
        """A translator refused `failures` times in one direction, then working."""
        state = {"n": 0}

        class Stub:
            def __init__(self, source, target):
                self.source, self.target = source, target

            def translate(self, text):
                if self.target == refused_direction:
                    state["n"] += 1
                    if state["n"] <= failures:
                        raise TooMany()
                if self.target == "pt":
                    # Real Portuguese, so the spacy pass finds content words and
                    # the gloss path is genuinely exercised.
                    return "\n".join(f"O gato dorme. {l}" for l in text.split("\n"))
                return "\n".join(f"<en>{l}" for l in text.split("\n"))

        return Stub

    def test_single_batch_waits_and_retries_until_it_succeeds(self, book, monkeypatch, waits):
        monkeypatch.setattr(run, "GoogleTranslator", self._flaky(2, "pt"))
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True, rate_limit_wait=1)
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert len(rows) == 2, "cards should be produced once the throttle lifts"
        assert len(waits) >= 2, "the run must actually wait between attempts"

    def test_backoff_escalates_between_retries(self, book, monkeypatch, waits):
        monkeypatch.setattr(run, "GoogleTranslator", self._flaky(3, "pt"))
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True, rate_limit_wait=10)
        assert waits[:3] == [10, 20, 40]

    def test_glosses_are_retried_not_abandoned_on_first_refusal(self, book, monkeypatch, waits):
        monkeypatch.setattr(run, "GoogleTranslator", self._flaky(2, "en"))
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True, rate_limit_wait=1)
        deck = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert any("(<en>" in row[1] for row in deck), "glosses should arrive after the retry"

    def test_gloss_retries_honour_the_configured_give_up(self, book, monkeypatch, waits):
        """The limiter used to be rebuilt per batch with default settings."""
        monkeypatch.setattr(run, "GoogleTranslator", self._flaky(999, "en"))
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True,
                             rate_limit_wait=1, rate_limit_give_up=3)
        # Two waits, then the third refusal exhausts it.
        assert len(waits) == 2
        rows = read_tsv(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
        assert len(rows) == 2, "cards survive even when glosses are given up on"

    def test_no_empty_deck_is_written_when_nothing_translated(self, book, monkeypatch, waits):
        monkeypatch.setattr(run, "GoogleTranslator", self._flaky(999, "pt"))
        run.create_anki_deck(book, "gtts", translator_name="google", input_lang="en", front="english",
                             output_name="deck", no_cache=True,
                             rate_limit_wait=1, rate_limit_give_up=2)
        assert not os.path.exists(os.path.join(os.path.dirname(book), "deck_AnkiDeck.tsv"))
