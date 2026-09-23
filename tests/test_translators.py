"""Backend selection, throttling, and failover between services."""
import pytest

import run


class Stub:
    def __init__(self, results=None, name="stub"):
        self.results = list(results or [])
        self.calls = []
        self.name = name
        self.source = self.target = None

    def translate(self, text):
        self.calls.append(text)
        out = self.results.pop(0) if self.results else f"{self.name}:{text}"
        if isinstance(out, Exception):
            raise out
        return out


class TooMany(Exception):
    def __str__(self):
        return "Server Error: You made too many requests to the server."


class TestQuotaMessageDetection:
    @pytest.mark.parametrize("text", [
        "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY",
        "You used all available FREE translations for today",
        "QUERY LENGTH LIMIT EXCEEDED. MAX ALLOWED QUERY : 500 CHARS",
    ])
    def test_recognises_a_refusal_dressed_as_a_translation(self, text):
        """MyMemory returns these as the translated text, not as an error."""
        assert run.looks_like_quota_message(text) is True

    @pytest.mark.parametrize("text", ["cat", "the warning bell", "", None])
    def test_leaves_real_translations_alone(self, text):
        assert run.looks_like_quota_message(text) is False

    def test_a_quota_message_is_never_cached_as_a_translation(self):
        quota = "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"
        limiter = run.RateLimiter()
        assert run.translate_with_retry(Stub([quota]), "gato", limiter=limiter) is None
        assert limiter.throttled, "a quota message should count as a refusal"


class TestThrottledTranslator:
    def test_spaces_requests_apart(self, monkeypatch):
        slept = []
        monkeypatch.setattr(run.time, "sleep", lambda d: slept.append(d))
        clock = iter([0.0, 0.1, 0.1, 5.0, 5.0])
        monkeypatch.setattr(run.time, "monotonic", lambda: next(clock))
        t = run.ThrottledTranslator(Stub(), "svc", min_interval=1.5)
        t.translate("a")   # first request: nothing to wait for
        t.translate("b")   # only 0.1s later -> must wait
        t.translate("c")   # 4.9s later -> no wait
        assert slept == [pytest.approx(1.4)]

    def test_counts_characters_sent(self):
        t = run.ThrottledTranslator(Stub(), "svc", min_interval=0)
        t.translate("hello")
        t.translate("world!")
        assert t.chars_sent == 11

    def test_warns_once_past_the_budget(self, capsys):
        t = run.ThrottledTranslator(Stub(), "svc", min_interval=0, char_budget=5)
        t.translate("abcdefgh")
        t.translate("more")
        out = capsys.readouterr().out
        assert out.count("past its usual free daily allowance") == 1

    def test_passes_the_translation_through(self):
        t = run.ThrottledTranslator(Stub(["ola"]), "svc", min_interval=0)
        assert t.translate("hello") == "ola"


class TestFallbackTranslator:
    def test_uses_the_first_backend_while_it_works(self):
        a, b = Stub(name="a"), Stub(name="b")
        f = run.FallbackTranslator([("a", a), ("b", b)])
        assert f.translate("x") == "a:x"
        assert b.calls == []

    def test_switches_when_the_first_refuses(self, capsys):
        a, b = Stub([TooMany()], name="a"), Stub(name="b")
        f = run.FallbackTranslator([("a", a), ("b", b)])
        assert f.translate("x") == "b:x"
        assert "switching to b" in capsys.readouterr().out

    def test_switches_on_a_quota_message_too(self):
        a = Stub(["MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"], name="a")
        b = Stub(name="b")
        f = run.FallbackTranslator([("a", a), ("b", b)])
        assert f.translate("x") == "b:x"

    def test_the_switch_is_permanent_for_the_run(self):
        """Going back would only collect another refusal."""
        a, b = Stub([TooMany()], name="a"), Stub(name="b")
        f = run.FallbackTranslator([("a", a), ("b", b)])
        f.translate("x")
        f.translate("y")
        assert len(a.calls) == 1 and len(b.calls) == 2

    def test_the_last_backend_propagates_its_failure(self):
        a = Stub([TooMany()], name="a")
        f = run.FallbackTranslator([("a", a)])
        with pytest.raises(Exception):
            f.translate("x")


class TestBuildTranslator:
    def test_auto_chains_offline_then_the_online_services(self):
        t = run.build_translator("auto", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert [name for name, _ in t.backends] == ["argos", "google", "mymemory"]

    def test_a_named_service_is_used_alone(self):
        t = run.build_translator("google", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert [name for name, _ in t.backends] == ["google"]

    def test_mymemory_gets_locale_codes(self):
        t = run.build_translator("mymemory", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        inner = t.backends[0][1].inner
        assert inner.source == "pt-BR" and inner.target == "en-US"

    def test_mymemory_is_throttled_and_google_is_not(self):
        auto = run.build_translator("auto", "pt", "en", run.LANGUAGE_CONFIGS["pt"], delay=2.0)
        by_name = dict(auto.backends)
        google, mymemory = by_name["google"], by_name["mymemory"]
        assert google.min_interval == 0.0
        assert mymemory.min_interval == 2.0
        assert mymemory.char_budget == 5000

    @pytest.mark.parametrize("lang,code", [("pt", "pt-BR"), ("fr", "fr-FR"), ("ru", "ru-RU")])
    def test_every_language_has_a_mymemory_code(self, lang, code):
        assert run.LANGUAGE_CONFIGS[lang]["mymemory_code"] == code

    def test_rejects_an_unknown_service(self):
        with pytest.raises(ValueError, match="Unknown translator"):
            run.build_translator("babelfish", "pt", "en", run.LANGUAGE_CONFIGS["pt"])


class TestQuerySplitting:
    """MyMemory rejects anything over 500 characters, and callers batch by
    joining lines, so oversized requests are split here."""

    class Echo:
        def __init__(self):
            self.calls = []

        def translate(self, text):
            self.calls.append(text)
            return "\n".join(f"<{l}>" for l in text.split("\n"))

    def _wrap(self, cap):
        inner = self.Echo()
        return inner, run.ThrottledTranslator(inner, "svc", min_interval=0,
                                              max_query_chars=cap)

    def test_short_requests_are_sent_whole(self):
        inner, t = self._wrap(100)
        t.translate("alpha\nbravo")
        assert inner.calls == ["alpha\nbravo"]

    def test_oversized_requests_are_split_on_line_boundaries(self):
        inner, t = self._wrap(20)
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        t.translate("\n".join(words))
        assert len(inner.calls) > 1
        assert all(len(c) <= 20 for c in inner.calls)
        # No line may be broken across two requests.
        assert "\n".join(inner.calls).split("\n") == words

    def test_line_count_and_order_survive_the_split(self):
        """Callers map results back positionally, so this must hold exactly."""
        inner, t = self._wrap(20)
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        out = t.translate("\n".join(words))
        assert out.split("\n") == [f"<{w}>" for w in words]

    def test_a_single_overlong_line_is_still_sent(self):
        inner, t = self._wrap(10)
        t.translate("a" * 50)
        assert inner.calls == ["a" * 50]

    def test_no_cap_means_no_splitting(self):
        inner = self.Echo()
        t = run.ThrottledTranslator(inner, "svc", min_interval=0)
        t.translate("\n".join("word" for _ in range(200)))
        assert len(inner.calls) == 1

    def test_mymemory_is_built_with_a_cap_under_its_limit(self):
        t = run.build_translator("mymemory", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert 0 < t.backends[0][1].max_query_chars < 500


class TestArgosBackend:
    """The offline model. Argos treats Brazilian Portuguese ("pb") as a
    separate model from European ("pt"), and they differ sharply."""

    def test_auto_prefers_offline_then_falls_back_online(self):
        t = run.build_translator("auto", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert [n for n, _ in t.backends][0] == "argos"
        assert "google" in [n for n, _ in t.backends]

    @pytest.mark.parametrize("lang,code", [("pt", "pb"), ("fr", "fr"), ("ru", "ru")])
    def test_language_configs_name_an_argos_model(self, lang, code):
        assert run.LANGUAGE_CONFIGS[lang]["argos_code"] == code

    def test_portuguese_uses_the_brazilian_model_not_european(self):
        """European "pt" would give "Estas a ficar", which clashes with the
        Brazilian audio the decks use."""
        t = run.build_translator("argos", "en", "pt", run.LANGUAGE_CONFIGS["pt"])
        assert t.backends[0][1].inner.target == "pb"

    def test_english_is_passed_through_unchanged(self):
        t = run.build_translator("argos", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert t.backends[0][1].inner.target == "en"

    def test_offline_backend_is_not_throttled(self):
        """There is no service to be polite to."""
        t = run.build_translator("argos", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert t.backends[0][1].min_interval == 0.0

    def test_auto_skips_argos_when_the_model_is_missing(self, monkeypatch, capsys):
        def unavailable(*_a, **_k):
            raise RuntimeError("offline pb->en model not installed")

        monkeypatch.setattr(run, "ArgosTranslator", unavailable)
        t = run.build_translator("auto", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert [n for n, _ in t.backends] == ["google", "mymemory"]
        assert "Offline translation unavailable" in capsys.readouterr().out

    def test_explicitly_choosing_argos_surfaces_the_error(self, monkeypatch):
        """Silently falling back would hide that --translator argos did nothing."""
        def unavailable(*_a, **_k):
            raise RuntimeError("offline pb->en model not installed")

        monkeypatch.setattr(run, "ArgosTranslator", unavailable)
        with pytest.raises(RuntimeError, match="not installed"):
            run.build_translator("argos", "pt", "en", run.LANGUAGE_CONFIGS["pt"])

    def test_auto_does_not_download_but_explicit_does(self, monkeypatch):
        seen = {}

        class Spy:
            def __init__(self, source, target, allow_download=False):
                seen[(source, target)] = allow_download
                self.source, self.target = source, target

        monkeypatch.setattr(run, "ArgosTranslator", Spy)
        run.build_translator("auto", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert seen[("pb", "en")] is False
        run.build_translator("argos", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert seen[("pb", "en")] is True


class TestBackendStrategy:
    """Each service is a class describing how to address it; build_translator
    composes them without knowing the specifics."""

    def test_every_registered_backend_implements_the_interface(self):
        for name, backend in run.TRANSLATION_BACKENDS.items():
            assert issubclass(backend, run.TranslationBackend), name
            assert backend.name, name
            assert backend.connect is not run.TranslationBackend.connect, name

    def test_auto_order_only_names_registered_backends(self):
        assert set(run.AUTO_TRANSLATOR_ORDER) <= set(run.TRANSLATION_BACKENDS)

    @pytest.mark.parametrize("backend,code,expected", [
        (run.ArgosBackend, "pt", "pb"),        # Brazilian, not European
        (run.ArgosBackend, "en", "en"),
        (run.MyMemoryBackend, "pt", "pt-BR"),  # wants locales
        (run.MyMemoryBackend, "en", "en-US"),
        (run.GoogleBackend, "pt", "pt"),       # plain codes
        (run.GoogleBackend, "en", "en"),
    ])
    def test_each_backend_spells_languages_its_own_way(self, backend, code, expected):
        assert backend.code_for(code, run.LANGUAGE_CONFIGS["pt"]) == expected

    def test_only_mymemory_honours_the_delay_flag(self):
        cfg = run.LANGUAGE_CONFIGS["pt"]
        assert run.MyMemoryBackend.build("pt", "en", cfg, delay=9).min_interval == 9
        assert run.GoogleBackend.build("pt", "en", cfg, delay=9).min_interval == 0.0

    def test_a_new_backend_needs_no_change_to_build_translator(self, monkeypatch):
        """The point of the pattern: adding DeepL should be a class, not a branch."""
        class FakeDeepL(run.TranslationBackend):
            name = "FakeDeepL"
            min_interval = 0.25

            @staticmethod
            def connect(source, target, explicit):
                return Stub(name="deepl")

        monkeypatch.setitem(run.TRANSLATION_BACKENDS, "deepl", FakeDeepL)
        t = run.build_translator("deepl", "pt", "en", run.LANGUAGE_CONFIGS["pt"])
        assert t.translate("gato") == "deepl:gato"
        assert t.backends[0][1].min_interval == 0.25

    def test_backends_carry_their_own_service_limits(self):
        assert run.MyMemoryBackend.max_query_chars < 500
        assert run.MyMemoryBackend.char_budget == 5000
        # The offline model has neither.
        assert run.ArgosBackend.max_query_chars is None
        assert run.ArgosBackend.char_budget is None
