"""Vocabulary annotation. Needs the Portuguese spacy model."""
import pytest

import run

PT = run.LANGUAGE_CONFIGS["pt"]


def annotate(nlp, sentence, word):
    """The bullet for one word of a sentence, parsed in context."""
    for token in nlp(sentence):
        if token.text == word:
            return run.annotate_token(token, PT)
    raise AssertionError(f"{word!r} not found in {sentence!r}")


def tag_for(nlp, sentence, word):
    for token in nlp(sentence):
        if token.text == word:
            return run.match_diminutive(token, PT)
    raise AssertionError(f"{word!r} not found in {sentence!r}")


class TestDiminutives:
    @pytest.mark.parametrize(
        "sentence,word",
        [
            ("O gatinho dorme.", "gatinho"),
            ("O cachorrinho late.", "cachorrinho"),
            ("A casinha é azul.", "casinha"),
            ("Quero um cafezinho.", "cafezinho"),
        ],
    )
    def test_real_diminutives_are_tagged(self, pt_nlp, sentence, word):
        assert tag_for(pt_nlp, sentence, word) == "dim."

    @pytest.mark.parametrize(
        "sentence,word",
        [
            # -cao/-sao nouns: the single largest source of false augmentatives.
            ("A situação mudou.", "situação"),
            ("O coração bate.", "coração"),
            ("Preciso de informação.", "informação"),
            ("Achei uma solução.", "solução"),
            # Ordinary words that merely end in -inho/-inha.
            ("O caminho é longo.", "caminho"),
            ("O vizinho chegou.", "vizinho"),
            ("A linha está ocupada.", "linha"),
            ("Comprei farinha.", "farinha"),
            ("Bebi vinho.", "vinho"),
        ],
    )
    def test_ordinary_nouns_are_not_tagged(self, pt_nlp, sentence, word):
        assert tag_for(pt_nlp, sentence, word) is None

    def test_no_augmentatives_are_detected_for_portuguese(self):
        assert all(label == "dim." for _, label in PT["diminutive_suffixes"])


class TestNouns:
    def test_masculine_noun_gets_its_article(self, pt_nlp):
        assert "-> o gato" in annotate(pt_nlp, "O gato dorme.", "gato")

    def test_feminine_noun_gets_its_article(self, pt_nlp):
        assert "-> a casa" in annotate(pt_nlp, "A casa é azul.", "casa")

    def test_plural_is_tagged(self, pt_nlp):
        assert "pl." in annotate(pt_nlp, "As pessoas procuram o atalho.", "pessoas")

    def test_part_of_speech_is_shown(self, pt_nlp):
        assert "(noun" in annotate(pt_nlp, "O gato dorme.", "gato")


class TestVerbs:
    def test_conjugation_class_from_the_lemma(self, pt_nlp):
        assert "-ar" in annotate(pt_nlp, "As pessoas procuram o atalho.", "procuram")

    def test_person_and_number(self, pt_nlp):
        assert "3rd pl." in annotate(pt_nlp, "As pessoas procuram o atalho.", "procuram")

    def test_infinitive_is_tagged(self, pt_nlp):
        assert "inf." in annotate(pt_nlp, "Você não vai encontrar.", "encontrar")

    def test_gerund_is_tagged(self, pt_nlp):
        assert "gerund" in annotate(pt_nlp, "E se você veio aqui procurando isso.", "procurando")


class TestBuildVocabHtml:
    def test_only_content_words_appear(self, pt_nlp):
        html = run.build_vocab_html(pt_nlp("O gato dorme na casa."), PT)
        assert "gato" in html and "casa" in html
        # Determiners and prepositions are not vocabulary.
        assert "<b>O</b>" not in html

    def test_bullets_are_br_joined(self, pt_nlp):
        html = run.build_vocab_html(pt_nlp("O gato dorme."), PT)
        assert html.count("<br>") == html.count("•") - 1

    def test_duplicate_bullets_collapse(self, pt_nlp):
        html = run.build_vocab_html(pt_nlp("O gato viu o gato."), PT)
        assert html.count("<b>gato</b>") == 1

    def test_definitions_come_from_the_lemma_map(self, pt_nlp):
        html = run.build_vocab_html(pt_nlp("O gato dorme."), PT, {"gato": "cat"})
        assert "(cat)" in html

    def test_missing_definition_is_simply_omitted(self, pt_nlp):
        html = run.build_vocab_html(pt_nlp("O gato dorme."), PT, {})
        assert "()" not in html

    def test_sentence_without_content_words_yields_nothing(self, pt_nlp):
        assert run.build_vocab_html(pt_nlp("O e a."), PT) == ""


class TestHeadingCase:
    """Books set headings in title or full caps, which spacy reads as proper
    nouns -- dropping every word from the vocabulary list."""

    @pytest.mark.parametrize("text,expected", [
        ("O Caminho da Disciplina.", "o caminho da disciplina."),
        ("DISCIPLINA DEVE HAVER.", "disciplina deve haver."),
        ("A RAIZ", "a raiz"),
    ])
    def test_headings_are_lowercased(self, text, expected):
        assert run.normalize_case_for_nlp(text) == expected

    @pytest.mark.parametrize("text", [
        "E se você veio aqui procurando por isso:",
        "O atalho é uma mentira.",
        "Mais forte. Mais inteligente.",
    ])
    def test_ordinary_sentences_are_untouched(self, text):
        assert run.normalize_case_for_nlp(text) == text

    def test_a_real_proper_noun_sentence_survives(self):
        """One capitalised name in a normal sentence is not a heading."""
        text = "O Sonic correu muito rápido hoje."
        assert run.normalize_case_for_nlp(text) == text

    @pytest.mark.parametrize("text", ["", "   ", "123 456"])
    def test_textless_input_is_returned_unchanged(self, text):
        assert run.normalize_case_for_nlp(text) == text

    def test_a_title_cased_heading_yields_vocabulary(self, pt_nlp):
        raw = pt_nlp("O Caminho da Disciplina.")
        assert run.build_vocab_html(raw, PT) == "", "precondition: PROPN hides these"
        fixed = pt_nlp(run.normalize_case_for_nlp("O Caminho da Disciplina."))
        html = run.build_vocab_html(fixed, PT)
        assert "caminho" in html and "disciplina" in html


class TestGlossCleanup:
    """Translators treat a lone word as a sentence and capitalise/full-stop it."""

    @pytest.mark.parametrize("gloss,lemma,expected", [
        ("Here.", "aqui", "here"),
        ("Good.", "bom", "good"),
        ("Better", "melhor", "better"),
        ("Overcoming", "superar", "overcoming"),
        ("cat", "gato", "cat"),
    ])
    def test_sentence_casing_is_stripped(self, gloss, lemma, expected):
        assert run.clean_gloss(gloss, lemma) == expected

    @pytest.mark.parametrize("gloss,lemma", [("I need it.", "preciso"), ("I will.", "farei")])
    def test_the_english_pronoun_keeps_its_capital(self, gloss, lemma):
        assert run.clean_gloss(gloss, lemma).startswith("I")

    def test_a_capitalised_source_word_keeps_its_capital(self):
        assert run.clean_gloss("Brazil", "Brasil") == "Brazil"
        assert run.clean_gloss("DISCIPLINE", "DISCIPLINA") == "DISCIPLINE"

    @pytest.mark.parametrize("gloss", ["", None, "   ", "."])
    def test_empty_glosses_stay_empty(self, gloss):
        assert run.clean_gloss(gloss, "x") == ""

    def test_the_cache_keeps_the_raw_value(self, pt_nlp):
        """Cleanup happens at render time, so a better cleanup later needs no refetch."""
        html = run.build_vocab_html(pt_nlp("Estou aqui."), PT, {"aqui": "Here."})
        assert "(here)" in html
