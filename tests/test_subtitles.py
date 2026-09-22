"""Subtitle parsing. These guard behaviour that predates ebook support."""
import pytest

import run


class TestParseSrtTimestamp:
    def test_parses_a_span(self):
        assert run.parse_srt_timestamp("00:00:01,500 --> 00:00:03,000") == (1500, 3000)

    def test_parses_hours_and_minutes(self):
        assert run.parse_srt_timestamp("01:02:03,004 --> 01:02:04,005") == (
            3723004, 3724005,
        )

    @pytest.mark.parametrize("line", ["not a timestamp", "", "1", "00:00:01 --> 00:00:02"])
    def test_returns_none_for_non_timestamps(self, line):
        assert run.parse_srt_timestamp(line) is None


class TestCleanText:
    def test_strips_html(self):
        assert run.clean_text("<i>Ola</i> mundo") == "Ola mundo"

    def test_strips_source_tags(self):
        assert run.clean_text("[source:abc] Ola") == "Ola"

    def test_strips_bracketed_annotations(self):
        assert run.clean_text("[explosao distante] Corre!") == "Corre!"

    def test_keeps_annotations_when_asked(self):
        out = run.clean_text("[Sonic] Corre!", strip_annotations=False)
        assert out == "[Sonic] Corre!"

    def test_removes_dialogue_dash_stranded_by_a_removed_annotation(self):
        assert run.clean_text("- [risada]") == ""

    def test_collapses_whitespace(self):
        assert run.clean_text("Ola    mundo\n\n  amigo") == "Ola mundo amigo"


class TestParseSubtitleBlock:
    def test_parses_a_standard_srt_block(self):
        block = "1\n00:00:01,000 --> 00:00:02,000\nOla mundo"
        assert run.parse_subtitle_block(block) == ("Ola mundo", (1000, 2000))

    def test_parses_a_vtt_block_without_a_cue_id(self):
        """VTT has no cue number, so the timestamp is found by scanning."""
        block = "00:00:01.000 --> 00:00:02.000\nOla mundo"
        result = run.parse_subtitle_block(block)
        assert result is not None and result[0] == "Ola mundo"

    def test_joins_multi_line_dialogue(self):
        block = "1\n00:00:01,000 --> 00:00:02,000\nOla\nmundo"
        assert run.parse_subtitle_block(block)[0] == "Ola mundo"

    def test_annotation_only_block_is_dropped(self):
        block = "1\n00:00:01,000 --> 00:00:02,000\n[explosao distante]"
        assert run.parse_subtitle_block(block) is None

    def test_annotation_only_block_survives_with_annotations_kept(self):
        block = "1\n00:00:01,000 --> 00:00:02,000\n[explosao distante]"
        assert run.parse_subtitle_block(block, strip_annotations=False) is not None

    def test_speaker_tag_is_stripped_but_dialogue_kept(self):
        block = "1\n00:00:01,000 --> 00:00:02,000\n[Sonic] E tambem tem o Shadow."
        assert run.parse_subtitle_block(block)[0] == "E tambem tem o Shadow."

    def test_block_without_a_timestamp_is_rejected(self):
        assert run.parse_subtitle_block("1\njust text") is None
