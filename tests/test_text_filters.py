"""Book text cleanup: front-matter filtering, fragment merging, slugs."""
import pytest

import run


class TestFilterBookBlocks:
    def test_drops_front_matter_by_section_name(self):
        blocks = [
            ("cover.xhtml", "Cover image"),
            ("contents.xhtml", "Chapter One"),
            ("copyright.xhtml", "Some notice"),
            ("chapter1.xhtml", "Real prose."),
        ]
        assert run.filter_book_blocks(blocks) == ["Real prose."]

    def test_drops_part_dividers_but_not_chapters(self):
        blocks = [("part1.xhtml", "PART ONE"), ("chapter12.xhtml", "Real prose.")]
        assert run.filter_book_blocks(blocks) == ["Real prose."]

    @pytest.mark.parametrize(
        "text",
        [
            "eISBN 9781250156952",
            "Produced by the Stonesong Press, LLC",
            "First Edition: October 2017",
            "All rights reserved.",
            "Table of Contents",
            "Begin Reading",
            "Thank you for buying this ebook",
        ],
    )
    def test_drops_boilerplate_by_content(self, text):
        """MOBI has no section names, so content patterns are the only signal."""
        assert run.filter_book_blocks([("book.mobi", text)]) == []

    def test_keeps_ordinary_prose(self):
        blocks = [("book.mobi", "Discipline starts with waking up early.")]
        assert run.filter_book_blocks(blocks) == ["Discipline starts with waking up early."]

    def test_keep_front_matter_disables_all_filtering(self):
        blocks = [("copyright.xhtml", "eISBN 123"), ("chapter1.xhtml", "Prose.")]
        assert run.filter_book_blocks(blocks, keep_front_matter=True) == ["eISBN 123", "Prose."]


class TestMergeFragments:
    def test_joins_a_fragment_into_the_following_block(self):
        texts = ["And if you want to take the easy road,", "it won't take you there."]
        assert run.merge_fragments(texts) == [
            "And if you want to take the easy road, it won't take you there."
        ]

    def test_leaves_complete_sentences_alone(self):
        texts = ["It really does.", "But that is the beginning."]
        assert run.merge_fragments(texts) == texts

    def test_chains_several_consecutive_fragments(self):
        texts = ["One—", "two—", "three."]
        assert run.merge_fragments(texts) == ["One— two— three."]

    @pytest.mark.parametrize("ending", [".", "!", "?", ":", "…", '"', "'", "”", "’", ")"])
    def test_every_terminal_punctuation_mark_ends_a_block(self, ending):
        texts = [f"Complete{ending}", "Next one."]
        assert run.merge_fragments(texts) == texts

    def test_allcaps_headings_are_never_merged(self):
        """Headings lack terminal punctuation but stand on their own."""
        texts = ["BAD INSTINCTS", "Discipline starts early."]
        assert run.merge_fragments(texts) == texts

    def test_a_heading_flushes_a_pending_fragment(self):
        texts = ["a dangling fragment—", "REGRET", "Next sentence."]
        assert run.merge_fragments(texts) == [
            "a dangling fragment—", "REGRET", "Next sentence."
        ]

    def test_trailing_fragment_is_still_emitted(self):
        assert run.merge_fragments(["ends without punctuation"]) == [
            "ends without punctuation"
        ]

    def test_max_chars_caps_a_runaway_merge(self):
        texts = ["x" * 400, "y" * 400, "done."]
        merged = run.merge_fragments(texts, max_chars=600)
        assert len(merged) == 2
        assert merged[1] == "done."

    def test_empty_input(self):
        assert run.merge_fragments([]) == []


class TestSlugify:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Discipline Equals Freedom", "discipline-equals-freedom"),
            ("Disciplina é liberdade", "disciplina-e-liberdade"),
            ("St. Martin's Press -- 2017", "st-martin-s-press-2017"),
            ("  leading and trailing  ", "leading-and-trailing"),
            ("Anna’s Archive", "anna-s-archive"),
        ],
    )
    def test_produces_a_filesystem_safe_name(self, raw, expected):
        assert run.slugify(raw) == expected

    def test_truncates_to_max_len_without_a_trailing_dash(self):
        out = run.slugify("a" * 30 + " " + "b" * 40, max_len=31)
        assert len(out) <= 31
        assert not out.endswith("-")

    @pytest.mark.parametrize("raw", ["", "---", "!!!"])
    def test_falls_back_when_nothing_survives(self, raw):
        assert run.slugify(raw) == "deck"
