"""EPUB/MOBI parsing: block extraction, spine order, PalmDOC decompression."""
import struct

import pytest

import run


class TestExtractHtmlBlocks:
    def test_closed_paragraphs(self):
        html = "<p>First line</p><p>Second line</p>"
        assert run.extract_html_blocks(html) == ["First line", "Second line"]

    def test_unclosed_paragraphs(self):
        """HTMLParser does not imply end tags, and ebook markup is sloppy.

        Without explicit handling the whole document collapses into one block.
        """
        html = "<p>First<p>Second<p>Third"
        assert run.extract_html_blocks(html) == ["First", "Second", "Third"]

    def test_headings_and_list_items(self):
        html = "<h1>Title</h1><p>Body</p><ul><li>One</li><li>Two</li></ul>"
        assert run.extract_html_blocks(html) == ["Title", "Body", "One", "Two"]

    def test_nested_block_flushes_once(self):
        html = "<blockquote><p>Quoted text</p></blockquote>"
        assert run.extract_html_blocks(html) == ["Quoted text"]

    def test_inline_tags_are_stripped_not_split(self):
        html = "<p>Some <i>italic</i> and <b>bold</b> text</p>"
        assert run.extract_html_blocks(html) == ["Some italic and bold text"]

    def test_br_becomes_a_space(self):
        assert run.extract_html_blocks("<p>Line one<br/>line two</p>") == ["Line one line two"]

    def test_entities_are_unescaped(self):
        assert run.extract_html_blocks("<p>Caf&#233; &amp; cr&#232;me</p>") == ["Café & crème"]

    def test_whitespace_is_collapsed(self):
        assert run.extract_html_blocks("<p>  spaced\n\n   out  </p>") == ["spaced out"]

    def test_empty_blocks_are_dropped(self):
        html = "<p></p><p>   </p><p>Real</p>"
        assert run.extract_html_blocks(html) == ["Real"]

    def test_text_outside_any_block_is_ignored(self):
        assert run.extract_html_blocks("loose text<p>In a block</p>") == ["In a block"]


class TestParseEpub:
    def test_reads_in_spine_order_not_zip_order(self, make_epub):
        path = make_epub(
            [("a.xhtml", "<p>Alpha</p>"), ("b.xhtml", "<p>Beta</p>")],
            spine=["b.xhtml", "a.xhtml"],
        )
        assert [text for _, text in run.parse_epub(path)] == ["Beta", "Alpha"]

    def test_reports_the_section_filename(self, make_epub):
        path = make_epub([("chapter1.xhtml", "<p>Body</p>")])
        assert run.parse_epub(path) == [("chapter1.xhtml", "Body")]

    def test_spine_entry_missing_from_manifest_is_skipped(self, make_epub):
        path = make_epub([("a.xhtml", "<p>Alpha</p>")], spine=["a.xhtml", "ghost.xhtml"])
        assert [t for _, t in run.parse_epub(path)] == ["Alpha"]

    def test_rejects_epub_without_rootfile(self, tmp_path):
        import zipfile
        path = tmp_path / "broken.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("META-INF/container.xml", "<container></container>")
        with pytest.raises(ValueError, match="rootfile"):
            run.parse_epub(str(path))


class TestPalmdocDecompress:
    def test_literal_bytes_pass_through(self):
        assert run._palmdoc_decompress(b"abc") == b"abc"

    def test_literal_run_marker_copies_following_bytes(self):
        # A leading byte of 1..8 means "copy that many bytes verbatim".
        assert run._palmdoc_decompress(bytes([3]) + b"xyz") == b"xyz"

    def test_high_bytes_expand_to_space_plus_character(self):
        # 0xC0..0xFF means a space followed by (byte ^ 0x80).
        assert run._palmdoc_decompress(bytes([ord("a") ^ 0x80])) == b" a"

    def test_length_distance_pair_repeats_earlier_text(self):
        # 0x80..0xBF is a 16-bit length/distance back-reference.
        pair = (0x80 << 8) | (3 << 3) | (3 - 3)  # distance 3, length 3
        data = b"abc" + struct.pack(">H", pair)
        assert run._palmdoc_decompress(data) == b"abcabc"

    def test_truncated_pair_does_not_raise(self):
        assert run._palmdoc_decompress(b"ab\x80") == b"ab"

    def test_out_of_range_distance_does_not_raise(self):
        pair = (0x80 << 8) | (99 << 3)
        assert run._palmdoc_decompress(struct.pack(">H", pair)) == b""


class TestMobiTrailingSize:
    def test_no_flags_means_no_trailing_bytes(self):
        assert run._mobi_trailing_size(b"some text", 0) == 0

    def test_multibyte_overlap_flag_reads_the_final_two_bits(self):
        assert run._mobi_trailing_size(b"text" + bytes([0x02]), 1) == 3

    def test_varint_flag_reads_a_backwards_encoded_length(self):
        assert run._mobi_trailing_size(b"A" * 10 + bytes([0x84]), 2) == 4


class TestParseMobi:
    def test_extracts_blocks(self, make_mobi):
        path = make_mobi("<html><body><p>First</p><p>Second</p></body></html>")
        assert [text for _, text in run.parse_mobi(path)] == ["First", "Second"]

    def test_section_is_the_filename(self, make_mobi):
        path = make_mobi("<html><body><p>Only</p></body></html>", name="novel.mobi")
        assert run.parse_mobi(path)[0][0] == "novel.mobi"


class TestParseBook:
    def test_dispatches_on_extension(self, make_epub, make_mobi):
        assert run.parse_book(make_epub([("a.xhtml", "<p>E</p>")])) == [("a.xhtml", "E")]
        assert [t for _, t in run.parse_book(make_mobi("<body><p>M</p></body>"))] == ["M"]

    @pytest.mark.parametrize("name", ["book.pdf", "book.txt", "book"])
    def test_rejects_unsupported_formats(self, tmp_path, name):
        path = tmp_path / name
        path.write_text("x")
        with pytest.raises(ValueError, match="Unsupported ebook format"):
            run.parse_book(str(path))
