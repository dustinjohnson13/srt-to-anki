"""Shared fixtures.

The tests import run.py directly; it is a single top-level module, so the
repository root has to be on sys.path.
"""
import os
import struct
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run  # noqa: E402


@pytest.fixture(scope="session")
def pt_nlp():
    """The Portuguese spacy model, skipped when it is not installed."""
    spacy = pytest.importorskip("spacy")
    try:
        return spacy.load("pt_core_news_sm")
    except OSError:
        pytest.skip("pt_core_news_sm not installed")


@pytest.fixture
def make_epub(tmp_path):
    """Build a minimal EPUB and return its path.

    `docs` is an ordered list of (filename, body_html); `spine` names the
    reading order, which the fixture deliberately writes to the zip in a
    different order so tests can prove spine order wins.
    """
    def _make(docs, spine=None, name="book.epub"):
        spine = spine if spine is not None else [d[0] for d in docs]
        path = tmp_path / name
        manifest = "\n".join(
            f'<item id="{fn.replace(".", "_")}" href="{fn}" media-type="application/xhtml+xml"/>'
            for fn, _ in docs
        )
        itemrefs = "\n".join(f'<itemref idref="{fn.replace(".", "_")}"/>' for fn in spine)
        opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata/>
  <manifest>{manifest}</manifest>
  <spine>{itemrefs}</spine>
</package>"""
        container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            # Reversed, so zip order never coincides with spine order.
            for fn, body in reversed(docs):
                z.writestr(
                    f"OEBPS/{fn}",
                    f"<html><head><title>t</title></head><body>{body}</body></html>",
                )
        return str(path)

    return _make


@pytest.fixture
def make_mobi(tmp_path):
    """Build a minimal uncompressed MOBI/PalmDOC file and return its path."""
    def _make(html, name="book.mobi"):
        path = tmp_path / name
        text = html.encode("utf-8")
        # One text record keeps the offset table trivial.
        records = [b"", text]  # record 0 is the header, filled in below

        header_len = 24  # < 0xE4, so parse_mobi reads no extra-data flags
        rec0 = struct.pack(">HHIHHI", 1, 0, len(text), 1, 4096, 0)
        rec0 += b"MOBI" + struct.pack(">I", header_len)
        records[0] = rec0

        n = len(records)
        pdb = bytearray(78 + 8 * n)
        pdb[0:6] = b"testdb"
        struct.pack_into(">H", pdb, 76, n)
        offset = 78 + 8 * n
        for i, rec in enumerate(records):
            struct.pack_into(">I", pdb, 78 + i * 8, offset)
            struct.pack_into(">B", pdb, 82 + i * 8, 0)
            offset += len(rec)
        path.write_bytes(bytes(pdb) + b"".join(records))
        return str(path)

    return _make
