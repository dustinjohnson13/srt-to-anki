import csv
import json
import re
import time
import argparse
import os
import random
import sys
import html
import hashlib
import posixpath
import struct
import unicodedata
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from deep_translator import GoogleTranslator

try:
    from deep_translator.exceptions import TooManyRequests
except ImportError:  # older deep-translator
    TooManyRequests = None
import spacy
from gtts import gTTS

VOCAB_POS = {"VERB", "NOUN", "ADJ", "ADV"}

# Morphology label maps shared by the annotation pass. A language config may
# override the tense map ("Past" is a preterite in Romance, plain past in
# Russian); the rest are language-neutral.
TENSE_LABELS = {"Pres": "present", "Past": "preterite", "Imp": "imperfect", "Fut": "future", "Pqp": "pluperfect"}
MOOD_LABELS = {"Sub": "subjunctive", "Imp": "imperative", "Cnd": "conditional"}
CASE_LABELS = {
    "Nom": "nom.", "Gen": "gen.", "Dat": "dat.", "Acc": "acc.",
    "Ins": "instr.", "Loc": "prep.", "Voc": "voc.", "Par": "part.",
}
ASPECT_LABELS = {"Imp": "impf.", "Perf": "perf."}
# spacy reports Person numerically for pt/fr but as words for ru.
PERSON_LABELS = {"1": "1st", "2": "2nd", "3": "3rd", "First": "1st", "Second": "2nd", "Third": "3rd"}

SRT_TIMESTAMP_RE = re.compile(
    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
)

# Bracketed subtitle annotations: sound effects ([explosao distante]) and
# speaker/delivery tags ([Sonic], [ecoa]). Stripped by default; a block that is
# nothing but annotations is dropped rather than turned into a card.
ANNOTATION_RE = re.compile(r'\[[^\]]*\]')


LANGUAGE_CONFIGS = {
    "pt": {
        "spacy_model": "pt_core_news_sm",
        "gtts_lang": "pt",
        "gtts_tld": "com.br",
        "translator_source": "pt",
        "mymemory_code": "pt-BR",
        "argos_code": "pb",
        "gender_articles": {"Masc": "o", "Fem": "a"},
        "verb_suffixes": ["ar", "er", "ir"],
        "tts_voices": {
            "google-cloud": ("pt-BR", "pt-BR-Neural2-A"),
            "azure": "pt-BR-FranciscaNeural",
            "polly": ("Camila", "pt-BR"),
        },
        # spacy does not lemmatise "gatinho" back to "gato" -- a Portuguese
        # diminutive is its own lexeme to the tagger -- so the suffix is
        # matched on the lemma, as for Russian.
        "diminutive_match": "lemma",
        # Deliberately short. Augmentatives are not detected at all: -ão ends
        # thousands of ordinary nouns (every -ção and -são), so "coração" and
        # "informação" would be tagged as augmentations of nothing, and a
        # wrong tag is worse than a missing one. -ito/-ita are dropped for the
        # same reason ("muito", "direito", "visita").
        "diminutive_suffixes": [
            ("zinho", "dim."), ("zinha", "dim."), ("zinhos", "dim."), ("zinhas", "dim."),
            ("inho", "dim."), ("inha", "dim."), ("inhos", "dim."), ("inhas", "dim."),
        ],
        # Ordinary words that merely happen to end in -inho/-inha. Not
        # exhaustive -- a precision aid for the frequent ones.
        "diminutive_exceptions": {
            "caminho", "vinho", "vizinho", "vizinha", "carinho", "ninho",
            "moinho", "pinho", "linho", "sozinho", "espinho", "padrinho",
            "sobrinho", "focinho", "golfinho", "adivinho",
            "linha", "farinha", "rainha", "galinha", "cozinha", "campainha",
            "bainha", "marinha", "andorinha", "sardinha", "minha", "vinha",
            "tinha",
        },
    },
    "fr": {
        "spacy_model": "fr_core_news_sm",
        "gtts_lang": "fr",
        "gtts_tld": None,
        "translator_source": "fr",
        "mymemory_code": "fr-FR",
        "argos_code": "fr",
        "gender_articles": {"Masc": "le", "Fem": "la"},
        "verb_suffixes": ["er", "ir", "re", "oir"],
        "tts_voices": {
            "google-cloud": ("fr-FR", "fr-FR-Neural2-A"),
            "azure": "fr-FR-DeniseNeural",
            "polly": ("Lea", "fr-FR"),
        },
        # Disabled. French diminutives are barely productive, while -ette,
        # -et, -eau and -ot end a very large number of ordinary nouns
        # ("recette", "objet", "bureau", "mot"). Detecting them by suffix
        # mislabels far more words than it catches.
        "diminutive_suffixes": [],
    },
    "ru": {
        "spacy_model": "ru_core_news_sm",
        "gtts_lang": "ru",
        "gtts_tld": None,
        "translator_source": "ru",
        "mymemory_code": "ru-RU",
        "argos_code": "ru",
        # Russian has no articles, so gender rides along as a tag instead.
        "gender_articles": {},
        "gender_tags": {"Masc": "m.", "Fem": "f.", "Neut": "n."},
        "show_case": True,
        "show_aspect": True,
        # Reflexives carry the particle in the lemma ("двигаться"); it is
        # stripped before the conjugation class is matched.
        "reflexive_suffixes": ["ся", "сь"],
        # Longest first: -овать has to win over the -ать it ends with.
        "verb_suffixes": [
            "овать", "евать", "ывать", "ивать",
            "ать", "ять", "еть", "ить", "оть", "уть", "ти", "чь",
        ],
        "tense_map": {"Pres": "present", "Past": "past", "Fut": "future"},
        "tts_voices": {
            "google-cloud": ("ru-RU", "ru-RU-Wavenet-A"),
            "azure": "ru-RU-SvetlanaNeural",
            "polly": ("Tatyana", "ru-RU"),
        },
        # Polly has no neural Russian voice; Tatyana is standard-engine only.
        "polly_engine": "standard",
        # Russian diminutives are their own lexemes ("домик" is not an inflected
        # "дом"), so the suffix is matched on the lemma, not the surface form.
        "diminutive_match": "lemma",
        # Deliberately short. The productive suffixes (-ик, -ок, -ка, -ушка,
        # -ище) are also the endings of ordinary nouns -- "ребёнок" is not a
        # diminutive, "чудовище" is not an augmentative -- and a wrong tag is
        # worse than a missing one, so only high-precision suffixes are listed.
        "diminutive_suffixes": [
            ("енька", "dim."), ("онька", "dim."), ("ечко", "dim."), ("ышко", "dim."),
        ],
    },
}


def parse_subtitle_block(block, strip_annotations=True):
    """Parse one subtitle block from SRT or VTT format.

    Finds the timestamp line by scanning for '-->' rather than assuming a fixed
    line index. This handles VTT blocks (no cue ID, optional positioning metadata)
    as well as standard SRT blocks.

    Returns (text, timestamp_ms_tuple_or_None), or None if the block has no
    timestamp line (e.g. WEBVTT header, NOTE blocks).
    """
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if "-->" in line:
            timestamp = parse_srt_timestamp(line)
            text = clean_text("\n".join(lines[i + 1:]), strip_annotations=strip_annotations)
            return (text, timestamp) if text else None
    return None


def parse_srt_texts(filepath, strip_annotations=True):
    """Parse an SRT or VTT file and return a list of cleaned subtitle texts in block order."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(filepath, "r", encoding=encoding) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        print(f"Error: Could not decode translation SRT file.")
        sys.exit(1)

    texts = []
    for block in content.strip().split("\n\n"):
        result = parse_subtitle_block(block, strip_annotations=strip_annotations)
        if result:
            texts.append(result[0])
    return texts



def clean_text(text, strip_annotations=True):
    text = re.sub(r'\[source:[^\]]*\]', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    if strip_annotations:
        text = ANNOTATION_RE.sub(' ', text)
    text = re.sub(r'\s+', ' ', text)
    # A stripped annotation can strand dialogue dashes at either end
    # ("- [risada]" or "Ta bem, Sonic. - [sapo coaxa]").
    text = re.sub(r'^[\s\-\u2013\u2014]+', '', text)
    text = re.sub(r'[\s\-\u2013\u2014]+$', '', text)
    return text.strip()


def parse_srt_timestamp(line):
    m = SRT_TIMESTAMP_RE.match(line.strip())
    if not m:
        return None
    parts = [int(g) for g in m.groups()]
    start_ms = parts[0] * 3600000 + parts[1] * 60000 + parts[2] * 1000 + parts[3]
    end_ms = parts[4] * 3600000 + parts[5] * 60000 + parts[6] * 1000 + parts[7]
    return (start_ms, end_ms)


BOOK_EXTENSIONS = {".epub", ".mobi", ".azw", ".prc"}

# Spine documents whose names mark front/back matter rather than body text.
FRONT_MATTER_RE = re.compile(
    r"(cover|title|toc|contents|copyright|dedication|newsletter|author|"
    r"endpaper|frontmatter|backmatter|colophon|acknowledg|part\d+|"
    r"fmtext|halftitle|epigraph|praise|alsoby|imprint|promo|advert)",
    re.I,
)

# Content-level front/back matter. Formats without section names (MOBI) have
# nothing else to go on, so the same patterns are matched against the text.
BOILERPLATE_RE = re.compile(
    r"(^\s*e?ISBN\b|Produced by|First Edition:|First e-?[Bb]ook Edition|"
    r"All rights reserved|^www\.|macmillan|^Table of Contents$|^Begin Reading$|"
    r"^Start$|Thank you for buying|^Photos by)",
    re.I,
)

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li"}

# Blocks that an unclosed sibling implicitly ends.
SELF_CLOSING_BLOCKS = {"p", "li"}

# A block not ending in one of these continues into the next block.
TERMINAL_PUNCT = tuple(".!?:…\"'”’)")


class _BlockExtractor(HTMLParser):
    """Collects the text of each block-level element as a single string."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._depth = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in SELF_CLOSING_BLOCKS and self._depth:
            # HTMLParser does not imply end tags, and sloppy ebook markup
            # leaves <p> unclosed. Without this the rest of the document
            # accumulates into one enormous block.
            self._flush()
        elif tag in BLOCK_TAGS:
            # Nested blocks (a <p> inside a <blockquote>) flush as one block.
            self._depth += 1
        elif tag == "br" and self._depth:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._flush()

    def handle_data(self, data):
        if self._depth:
            self._buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if text:
            self.blocks.append(text)
        self._buf = []

    def close(self):
        super().close()
        if self._depth:
            self._flush()


def extract_html_blocks(html_text):
    """Block-level text of an HTML fragment, in document order."""
    parser = _BlockExtractor()
    try:
        parser.feed(html_text)
        parser.close()
        return parser.blocks
    except Exception:
        # Malformed markup shouldn't cost us the whole document.
        return [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))).strip()
            for m in re.finditer(r"<(p|h[1-6])[^>]*>(.*?)</\1>", html_text, re.S | re.I)
        ]


def parse_epub(filepath):
    """Read an EPUB in spine order -> [(section_name, text), ...]."""
    out = []
    with zipfile.ZipFile(filepath) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(
            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        if rootfile is None:
            raise ValueError("EPUB has no rootfile in META-INF/container.xml")
        opf_path = rootfile.get("full-path")
        opf = ET.fromstring(z.read(opf_path))
        ns = {"o": "http://www.idpf.org/2007/opf"}
        base = posixpath.dirname(opf_path)
        manifest = {
            item.get("id"): item.get("href")
            for item in opf.findall(".//o:manifest/o:item", ns)
        }
        # The spine is reading order; zip entry order is arbitrary.
        for itemref in opf.findall(".//o:spine/o:itemref", ns):
            href = manifest.get(itemref.get("idref"))
            if not href:
                continue
            full = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
            try:
                raw = z.read(full).decode("utf-8", "replace")
            except KeyError:
                continue
            body = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
            section = posixpath.basename(full)
            for text in extract_html_blocks(body.group(1) if body else raw):
                out.append((section, text))
    return out


def _palmdoc_decompress(data):
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0:
            out.append(0)
        elif c <= 8:
            out += data[i : i + c]
            i += c
        elif c <= 0x7F:
            out.append(c)
        elif c <= 0xBF:
            if i >= n:
                break
            c = (c << 8) | data[i]
            i += 1
            dist = (c >> 3) & 0x07FF
            length = (c & 7) + 3
            if not 0 < dist <= len(out):
                break
            for _ in range(length):
                out.append(out[-dist])
        else:
            out.append(32)
            out.append(c ^ 0x80)
    return bytes(out)


def _mobi_trailing_size(data, flags):
    """Bytes of per-record trailing metadata that are not text."""
    num = 0
    for bit in (16, 8, 4, 2):
        if flags & bit:
            value = 0
            end = len(data) - num
            # A backwards-encoded varint sits at the end of the record.
            for k in range(max(0, end - 4), end):
                byte = data[k]
                if byte & 0x80:
                    value = 0
                value = (value << 7) | (byte & 0x7F)
            num += value
    if flags & 1:
        num += (data[len(data) - num - 1] & 0x3) + 1
    return num


def parse_mobi(filepath):
    """Read a MOBI/PalmDOC ebook -> [(section_name, text), ...]."""
    with open(filepath, "rb") as f:
        raw = f.read()
    record_count = struct.unpack(">H", raw[76:78])[0]
    offsets = [
        struct.unpack(">I", raw[78 + i * 8 : 82 + i * 8])[0] for i in range(record_count)
    ]
    offsets.append(len(raw))
    rec0 = raw[offsets[0] : offsets[1]]
    compression, _, text_len, text_records, _, _ = struct.unpack(">HHIHHI", rec0[:16])
    mobi_header_len = struct.unpack(">I", rec0[20:24])[0]
    # "Extra data flags" mark trailing bytes that must be stripped from each
    # record before decompression, or the decoder walks off the end.
    flags = struct.unpack(">H", rec0[0xF2:0xF4])[0] if mobi_header_len >= 0xE4 else 0
    body = bytearray()
    for i in range(1, text_records + 1):
        chunk = raw[offsets[i] : offsets[i + 1]]
        chunk = chunk[: len(chunk) - _mobi_trailing_size(chunk, flags)]
        body += _palmdoc_decompress(chunk) if compression == 2 else chunk
    text = bytes(body)[:text_len].decode("utf-8", "replace")
    section = os.path.basename(filepath)
    return [(section, t) for t in extract_html_blocks(text)]


def parse_book(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".epub":
        return parse_epub(filepath)
    if ext in (".mobi", ".azw", ".prc"):
        return parse_mobi(filepath)
    raise ValueError(f"Unsupported ebook format: {ext}")


def filter_book_blocks(blocks, keep_front_matter=False):
    """Drop the TOC, copyright page, newsletter ad and similar matter."""
    kept = []
    for section, text in blocks:
        if not keep_front_matter and (
            FRONT_MATTER_RE.search(section) or BOILERPLATE_RE.search(text)
        ):
            continue
        kept.append(text)
    return kept


def merge_fragments(texts, max_chars=600):
    """Join mid-sentence fragments into whole sentences.

    Books break sentences across lines for rhythm ("To push back-"), and a
    fragment translated on its own is nonsense. A block that does not end in
    terminal punctuation continues into the next one. ALL-CAPS headings also
    lack punctuation but stand alone, so they are never merged.
    """
    merged = []
    buf = ""
    for text in texts:
        if text.isupper():
            if buf:
                merged.append(buf)
                buf = ""
            merged.append(text)
            continue
        candidate = f"{buf} {text}".strip() if buf else text
        if text.rstrip().endswith(TERMINAL_PUNCT) or len(candidate) >= max_chars:
            merged.append(candidate)
            buf = ""
        else:
            buf = candidate
    if buf:
        merged.append(buf)
    return merged


def slugify(name, max_len=60):
    """A filesystem- and Anki-safe base name from an arbitrary book title."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return name[:max_len].strip("-") or "deck"


def slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms, offset_ms=0):
    actual_start = max(0, start_ms + offset_ms - padding_ms)
    actual_end = min(len(source_audio), end_ms + offset_ms + padding_ms)
    if actual_end <= actual_start:
        raise ValueError(f"Zero-duration clip: {actual_start}ms to {actual_end}ms")
    clip = source_audio[actual_start:actual_end]
    clip.export(output_path, format="mp3")


def generate_audio(text, filepath, provider, lang_config):
    if provider == "gtts":
        kwargs = {"text": text, "lang": lang_config["gtts_lang"], "slow": False}
        if lang_config["gtts_tld"]:
            kwargs["tld"] = lang_config["gtts_tld"]
        gTTS(**kwargs).save(filepath)

    elif provider == "google-cloud":
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        lang_code, voice_name = lang_config["tts_voices"]["google-cloud"]
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=lang_code,
                name=voice_name,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            ),
        )
        with open(filepath, "wb") as f:
            f.write(response.audio_content)

    elif provider == "azure":
        import azure.cognitiveservices.speech as speechsdk
        key = os.environ["AZURE_TTS_KEY"]
        region = os.environ["AZURE_TTS_REGION"]
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        speech_config.speech_synthesis_voice_name = lang_config["tts_voices"]["azure"]
        audio_config = speechsdk.audio.AudioOutputConfig(filename=filepath)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )
        result = synthesizer.speak_text_async(text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError(f"Azure TTS failed: {result.reason}")

    elif provider == "polly":
        import boto3
        client = boto3.client("polly")
        default_voice, default_lang_code = lang_config["tts_voices"]["polly"]
        voice_id = os.environ.get("POLLY_VOICE_ID", default_voice)
        lang_code = os.environ.get("POLLY_LANGUAGE_CODE", default_lang_code)
        speed = os.environ.get("POLLY_SPEED", "slow")
        engine = os.environ.get("POLLY_ENGINE", lang_config.get("polly_engine", "neural"))
        response = client.synthesize_speech(
            Text=f"<speak><prosody rate=\"{speed}\">{text}</prosody></speak>",
            TextType="ssml",
            OutputFormat="mp3",
            VoiceId=voice_id,
            Engine=engine,
            LanguageCode=lang_code,
        )
        with open(filepath, "wb") as f:
            f.write(response["AudioStream"].read())

    elif provider == "elevenlabs":
        from elevenlabs.client import ElevenLabs
        from elevenlabs import save
        from elevenlabs import VoiceSettings
        api_key = os.environ["ELEVENLABS_API_KEY"]
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
        speed = float(os.environ.get("ELEVENLABS_SPEED", "0.7"))
        client = ElevenLabs(api_key=api_key)
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(speed=speed),
        )
        save(audio, filepath)

    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


def detect_audio_offset(source_audio, srt_spans, max_lag_ms=120000, fps=100):
    """Estimate the constant offset between SRT timings and the audio track.

    Builds a crude voice-activity signal from the audio (frame energy above a
    rolling median, which suppresses steady music and ambience), builds a second
    signal from the subtitle spans, and cross-correlates the two via FFT to find
    the lag that lines them up.

    Returns (offset_ms, stats_dict). offset_ms is None if no confident peak was
    found. The returned offset uses the same sign convention as --audio-offset:
    positive means the audio runs later than the SRT claims.
    """
    import numpy as np

    sr = 8000
    npf = sr // fps

    mono = source_audio.set_channels(1).set_frame_rate(sr)
    full_scale = float(1 << (8 * mono.sample_width - 1))
    x = np.array(mono.get_array_of_samples(), dtype=np.float32) / full_scale
    n = len(x) // npf
    if n < fps * 30:
        return None, {"reason": "audio too short to align"}

    rms = np.sqrt((x[: n * npf].reshape(n, npf) ** 2).mean(axis=1)) + 1e-9
    log_energy = np.log(rms)

    # Subtract a rolling average so a loud music bed doesn't read as speech.
    window = fps * 15
    padded = np.pad(log_energy, (window // 2, window // 2), mode="edge")
    floor = np.convolve(padded, np.ones(window) / window, mode="same")
    floor = floor[window // 2 : window // 2 + n]
    vad = (log_energy - floor > 0.35).astype(np.float32)

    sub = np.zeros(n, dtype=np.float32)
    for start_ms, end_ms in srt_spans:
        a = int(start_ms / 1000 * fps)
        b = int(end_ms / 1000 * fps)
        if a < n:
            sub[a : min(b, n)] = 1.0

    if vad.max() == 0 or sub.max() == 0:
        return None, {"reason": "no usable activity in audio or subtitles"}

    def correlate(a, b, max_lag):
        a = a - a.mean()
        b = b - b.mean()
        size = 1
        while size < 2 * (len(a) + max_lag):
            size *= 2
        cc = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
        cc = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]])
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
        return np.arange(-max_lag, max_lag + 1), cc / denom

    max_lag = min(int(max_lag_ms / 1000 * fps), n - 1)
    lags, cc = correlate(vad, sub, max_lag)
    peak_i = int(cc.argmax())
    offset_ms = int(round(lags[peak_i] / fps * 1000))
    peak = float(cc[peak_i])
    z = float((peak - cc.mean()) / (cc.std() or 1.0))

    # Agreement between halves is the strongest cheap signal that the offset is
    # genuinely constant rather than a spurious correlation peak.
    half = n // 2
    halves = []
    for sl in (slice(0, half), slice(half, n)):
        h_lags, h_cc = correlate(vad[sl], sub[sl], max_lag)
        halves.append(int(round(h_lags[int(h_cc.argmax())] / fps * 1000)))

    stats = {
        "corr": peak,
        "z": z,
        "halves": halves,
        "spread_ms": abs(halves[0] - halves[1]),
    }
    confident = z >= 5.0 and stats["spread_ms"] <= 1000
    return (offset_ms if confident else None), stats


# MyMemory wants locale codes, and answers an exhausted quota with a warning
# *as the translated text* rather than an error -- which would otherwise be
# cached as a word's definition.
MYMEMORY_ENGLISH = "en-US"
QUOTA_MESSAGE_RE = re.compile(
    r"(MYMEMORY WARNING|YOU USED ALL AVAILABLE FREE TRANSLATIONS|"
    r"QUERY LENGTH LIMIT EXCEEDED|INVALID EMAIL PROVIDED)",
    re.I,
)


def looks_like_quota_message(text):
    """Whether a 'translation' is really the service telling us to stop."""
    return bool(text) and bool(QUOTA_MESSAGE_RE.search(str(text)))


class ThrottledTranslator:
    """Spaces out requests to a backend, and counts what we have spent.

    MyMemory's free tier is a courtesy rather than a contract: it asks for
    modest use and answers abuse with a block. Leaving a gap between requests
    keeps us inside that, and the character counter makes the daily allowance
    visible instead of discovering it as a garbled card.
    """

    def __init__(self, inner, name, min_interval=1.5, char_budget=None,
                 max_query_chars=None):
        self.inner = inner
        self.name = name
        self.min_interval = min_interval
        self.char_budget = char_budget
        # MyMemory rejects anything over 500 characters outright, and callers
        # batch by joining lines, so the split happens here rather than making
        # every caller know the limit.
        self.max_query_chars = max_query_chars
        self.chars_sent = 0
        self._last_call = None
        self._warned = False

    @property
    def source(self):
        return getattr(self.inner, "source", None)

    @property
    def target(self):
        return getattr(self.inner, "target", None)

    def translate(self, text):
        if self.max_query_chars and len(text) > self.max_query_chars:
            return "\n".join(
                self._send(group) for group in self._split(text)
            )
        return self._send(text)

    def _split(self, text):
        """Group whole lines into requests under the service's size limit.

        Splitting on line boundaries keeps the response line count equal to the
        request's, which is what callers map back positionally.
        """
        group, size = [], 0
        for line in text.split("\n"):
            if group and size + len(line) + 1 > self.max_query_chars:
                yield "\n".join(group)
                group, size = [], 0
            group.append(line)
            size += len(line) + 1
        if group:
            yield "\n".join(group)

    def _send(self, text):
        # Nothing to space the first request from.
        if self._last_call is not None:
            gap = self.min_interval - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
        self._last_call = time.monotonic()
        self.chars_sent += len(text)
        if (
            self.char_budget
            and self.chars_sent > self.char_budget
            and not self._warned
        ):
            self._warned = True
            print(
                f"  Note: about {self.chars_sent} characters sent to {self.name} this run, "
                f"past its usual free daily allowance of {self.char_budget}."
            )
        return self.inner.translate(text)


class FallbackTranslator:
    """Tries backends in order, moving on for good when one refuses us.

    Google's free translation endpoints currently answer programmatic requests
    with 429 regardless of address, so a run that starts there needs somewhere
    to go. The switch is permanent for the run: once a service has refused us,
    going back to it on the next batch would just collect another refusal.
    """

    def __init__(self, backends):
        self.backends = backends  # [(name, translator), ...]
        self.index = 0

    @property
    def name(self):
        return self.backends[self.index][0]

    def translate(self, text):
        while True:
            name, backend = self.backends[self.index]
            try:
                result = backend.translate(text)
            except Exception:
                if self.index + 1 < len(self.backends):
                    self.index += 1
                    print(
                        f"  {name} refused the request; switching to "
                        f"{self.backends[self.index][0]} for the rest of this run."
                    )
                    continue
                raise
            if looks_like_quota_message(result) and self.index + 1 < len(self.backends):
                self.index += 1
                print(
                    f"  {name} reports its quota is spent; switching to "
                    f"{self.backends[self.index][0]} for the rest of this run."
                )
                continue
            return result


class ArgosTranslator:
    """A local offline model. No network, no quota, no rate limit.

    Argos distinguishes European Portuguese ("pt") from Brazilian ("pb") as
    separate models, and they differ sharply -- "Estas a ficar mais forte"
    against "Voce esta ficando mais forte" -- so the code comes from
    `argos_code` rather than reusing `translator_source`.
    """

    def __init__(self, source, target, allow_download=False):
        import argostranslate.package
        import argostranslate.translate

        self.source, self.target = source, target
        self._translate = argostranslate.translate.translate
        if not self._pair_installed(argostranslate.translate):
            if not allow_download:
                raise RuntimeError(
                    f"offline {source}->{target} model not installed"
                )
            print(f"  Downloading the offline {source}->{target} model (one time)...")
            argostranslate.package.update_package_index()
            package = next(
                (
                    x
                    for x in argostranslate.package.get_available_packages()
                    if x.from_code == source and x.to_code == target
                ),
                None,
            )
            if package is None:
                raise RuntimeError(f"no offline model published for {source}->{target}")
            argostranslate.package.install_from_path(package.download())

    def _pair_installed(self, argos_translate):
        langs = {l.code: l for l in argos_translate.get_installed_languages()}
        if self.source not in langs or self.target not in langs:
            return False
        return langs[self.source].get_translation(langs[self.target]) is not None

    def translate(self, text):
        return self._translate(text, self.source, self.target)


class TranslationBackend:
    """One translation service.

    A subclass says how to address the service and how politely to use it;
    `build_translator` composes them without knowing any of those details, so
    adding a service is a class plus a registry entry rather than another
    branch in a factory.
    """

    name = "backend"
    min_interval = 0.0        # seconds to leave between requests
    char_budget = None        # free allowance worth warning about
    max_query_chars = None    # hard per-request limit; split on line boundaries
    config_key = None         # LANGUAGE_CONFIGS key holding this service's code
    english = "en"            # how this service spells English
    honours_delay = False     # whether --translator-delay applies

    @classmethod
    def code_for(cls, code, lang_config):
        if code == "en":
            return cls.english
        return lang_config.get(cls.config_key, code) if cls.config_key else code

    @classmethod
    def build(cls, source, target, lang_config, explicit=False, delay=None):
        inner = cls.connect(
            cls.code_for(source, lang_config),
            cls.code_for(target, lang_config),
            explicit,
        )
        interval = delay if (cls.honours_delay and delay is not None) else cls.min_interval
        return ThrottledTranslator(
            inner,
            cls.name,
            min_interval=interval,
            char_budget=cls.char_budget,
            max_query_chars=cls.max_query_chars,
        )

    @staticmethod
    def connect(source, target, explicit):
        raise NotImplementedError


class ArgosBackend(TranslationBackend):
    """Local model: no network, no quota, no rate limit."""

    name = "Argos"
    config_key = "argos_code"

    @staticmethod
    def connect(source, target, explicit):
        # Only download when the user asked for this backend by name, so
        # "auto" never triggers a surprise several-hundred-megabyte fetch.
        return ArgosTranslator(source, target, allow_download=explicit)


class GoogleBackend(TranslationBackend):
    """The free endpoint deep-translator scrapes. Plain language codes."""

    name = "Google"

    @staticmethod
    def connect(source, target, explicit):
        return GoogleTranslator(source=source, target=target)


class MyMemoryBackend(TranslationBackend):
    """Keyless, but a small daily allowance and a hard 500-character query cap."""

    name = "MyMemory"
    config_key = "mymemory_code"
    english = "en-US"
    min_interval = 1.5
    honours_delay = True
    char_budget = 5000        # the anonymous daily allowance
    max_query_chars = 450     # its hard limit is 500

    @staticmethod
    def connect(source, target, explicit):
        from deep_translator import MyMemoryTranslator

        return MyMemoryTranslator(source=source, target=target)


TRANSLATION_BACKENDS = {
    "argos": ArgosBackend,
    "google": GoogleBackend,
    "mymemory": MyMemoryBackend,
}

# Offline first: it has no quota and cannot be throttled.
AUTO_TRANSLATOR_ORDER = ["argos", "google", "mymemory"]


def build_translator(spec, source, target, lang_config, delay=1.5):
    """A translator for `spec`: one service, or "auto" to chain them.

    `source` and `target` are plain codes ("pt", "en"); each backend converts
    them to whatever dialect it expects.
    """
    names = AUTO_TRANSLATOR_ORDER if spec == "auto" else [spec]
    backends = []
    for name in names:
        backend = TRANSLATION_BACKENDS.get(name)
        if backend is None:
            raise ValueError(f"Unknown translator: {name}")
        try:
            backends.append(
                (
                    name,
                    backend.build(
                        source, target, lang_config, explicit=(spec == name), delay=delay
                    ),
                )
            )
        except Exception as e:
            if spec != "auto":
                raise
            # In "auto" an unavailable service just means trying the next.
            if name == "argos":
                print(f"  Offline translation unavailable ({e}); using online services.")
    if not backends:
        raise RuntimeError("No translation backend is available")
    return FallbackTranslator(backends)


def is_rate_limited(exc):
    """Whether an exception is the endpoint refusing us for rate reasons."""
    if TooManyRequests is not None and isinstance(exc, TooManyRequests):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return "too many requests" in text or "toomanyrequests" in text or "429" in text


class RateLimiter:
    """Paces requests against an endpoint that throttles without saying for how long.

    Google's free translation endpoint answers a throttled request with a bare
    429: no Retry-After, no RateLimit-* headers. There is therefore no
    server-supplied delay to honour, and retrying straight into the block only
    seems to prolong it. So the strategy is an escalating, jittered, capped
    wait between batches, plus a limit on how many consecutive refusals to
    absorb before stopping and letting the caches carry progress to a later run.
    """

    def __init__(self, base_delay=60.0, cap=600.0, give_up_after=5):
        self.base_delay = base_delay
        self.cap = cap
        self.give_up_after = give_up_after
        self.consecutive = 0

    @property
    def throttled(self):
        return self.consecutive > 0

    @property
    def exhausted(self):
        return self.consecutive >= self.give_up_after

    @property
    def delay(self):
        return min(self.base_delay * (2 ** (self.consecutive - 1)), self.cap)

    def record_limit(self):
        self.consecutive += 1

    def record_success(self):
        self.consecutive = 0

    def wait(self, sleeper=None):
        # Jitter keeps repeated runs from retrying in lockstep.
        delay = self.delay * random.uniform(0.8, 1.2)
        print(
            f"  Rate limited. Waiting {delay:.0f}s before retrying "
            f"({self.consecutive}/{self.give_up_after} before giving up)..."
        )
        (sleeper or time.sleep)(delay)
        return delay


def translate_with_retry(translator, text, tries=4, base_delay=2.0, limiter=None):
    """Translate one string, retrying with exponential backoff.

    The free Google endpoint used by deep-translator throttles aggressively and
    raises TranslationNotFound for arbitrary inputs when it does, so a single
    attempt is not a reliable signal of failure. An explicit rate-limit refusal
    is different: it will not clear in the couple of seconds this backoff
    covers, so it returns immediately and lets the caller pace the retry.
    """
    delay = base_delay
    for attempt in range(1, tries + 1):
        try:
            result = translator.translate(text)
            if looks_like_quota_message(result):
                # The service answered with its quota warning as the text.
                if limiter:
                    limiter.record_limit()
                return None
            if result:
                if limiter:
                    limiter.record_success()
                return result
        except Exception as e:
            if is_rate_limited(e):
                if limiter:
                    limiter.record_limit()
                return None
        if attempt < tries:
            time.sleep(delay)
            delay *= 2
    return None


def translate_chunk(translator, chunk, limiter=None):
    """Translate a list of sentences, returning a same-length list.

    Fast path joins the chunk with newlines in one request. If that request
    fails or comes back with a different number of lines, fall back to
    per-sentence translation so one bad sentence costs one card instead of the
    whole batch. Untranslatable sentences come back as None.
    """
    joined = translate_with_retry(translator, "\n".join(chunk), limiter=limiter)
    if joined:
        lines = joined.split("\n")
        if len(lines) == len(chunk):
            return lines
        print(
            f"  Line mismatch (got {len(lines)}, expected {len(chunk)}); "
            "falling back to per-sentence translation..."
        )
    elif limiter is not None and limiter.throttled:
        # Throttled: the individual requests would all be refused the same way,
        # so skip the fallback rather than make 40 more of them.
        return [None] * len(chunk)
    else:
        print("  Batch request failed; falling back to per-sentence translation...")

    results = []
    for sentence in chunk:
        results.append(translate_with_retry(translator, sentence, tries=3, limiter=limiter))
        if limiter is not None and limiter.throttled:
            results.extend([None] * (len(chunk) - len(results)))
            break
        time.sleep(0.3)
    return results




COLON_CAPITAL_RE = re.compile(r"(:\s+)([A-Z])(?=[a-z])")


def prepare_for_translation(text):
    """Lower-case the word following a mid-sentence colon.

    Translation models frequently drop the clause *before* a colon when the
    word after it is capitalised: "Discipline: The root of all good qualities"
    comes back as "A raiz de todas as boas qualidades", losing "Discipline"
    altogether, while the same sentence with a lower-case "the" keeps it. This
    affects about an eighth of the blocks in a typical book.

    Only the text sent to the translator changes -- the card's own English side
    keeps its original capitalisation, and the cache stays keyed on the
    original. No adjustment back is needed: Portuguese sets a lower-case word
    after a colon anyway, so the output is more correct rather than less.

    Only Title-Cased words are touched. This book sets whole words in capitals
    for emphasis, and lower-casing just the first letter would give "gOOD"; a
    lone "I" is left alone for the same reason. A proper noun directly after a
    colon ("Murphy" here) is still lower-cased, which is cosmetic beside losing
    a whole clause.
    """
    return COLON_CAPITAL_RE.sub(lambda m: m.group(1) + m.group(2).lower(), text)


def normalize_case_for_nlp(text):
    """Lower-case a heading before tagging it.

    Books set headings in title or full caps, and spacy reads that casing as
    proper nouns: "O Caminho da Disciplina" tags as PROPN PROPN, so both words
    drop out of the vocabulary list entirely. The casing carries no
    grammatical meaning, so it is removed before tagging.
    """
    words = re.findall(r"[^\W\d_]{2,}", text)
    if not words:
        return text
    capitalised = sum(1 for w in words if w[0].isupper())
    if text.isupper() or capitalised / len(words) > 0.6:
        return text.lower()
    return text


def clean_gloss(gloss, lemma):
    """Tidy a one-word definition.

    Translators treat a lone word as a whole sentence and hand back "Here."
    or "Good." -- capitalised and full-stopped. The capital is kept only when
    the source word carries one itself, or when it is the English "I".
    """
    text = str(gloss or "").strip().rstrip(".").strip()
    if not text:
        return ""
    keeps_capital = (
        lemma[:1].isupper()
        or text.isupper()
        or text == "I"
        or text.startswith(("I ", "I'"))
    )
    if not keeps_capital and text[:1].isupper():
        text = text[0].lower() + text[1:]
    return text


def match_diminutive(token, lang_config):
    """The diminutive/augmentative label for a token, or None.

    Two strategies, because the morphology differs. A Romance diminutive formed
    by inflection ("surface", the default) only counts when the surface form
    actually differs from the lemma. A diminutive that is its own lexeme --
    Russian "домик", and in practice Portuguese "gatinho", neither of which
    spacy lemmatises back to its base -- has to be matched on the lemma itself
    ("lemma").
    """
    lemma_lower = token.lemma_.lower()
    # Check the surface form too: the small models mislemmatise often enough
    # ("farinha" -> "farinho") to slip an exception past a lemma-only lookup.
    exceptions = lang_config.get("diminutive_exceptions", ())
    if lemma_lower in exceptions or token.text.lower() in exceptions:
        return None
    if lang_config.get("diminutive_match") == "lemma":
        candidate = lemma_lower
    else:
        candidate = token.text.lower()
        if candidate == lemma_lower:
            return None
    for suffix, label in lang_config["diminutive_suffixes"]:
        if candidate.endswith(suffix):
            return label
    return None


def annotate_token(token, lang_config, lemma_translations=None):
    """One vocabulary bullet: surface form, lemma, definition and grammar tags."""
    lemma_translations = lemma_translations or {}
    en_def = clean_gloss(lemma_translations.get(token.lemma_, ""), token.lemma_)
    definition = f" ({en_def})" if en_def else ""
    lemma = token.lemma_
    morph = token.morph.to_dict()
    tags = []

    if token.pos_ == "NOUN":
        gender = morph.get("Gender", "")
        article = lang_config["gender_articles"].get(gender, "")
        if article:
            lemma = f"{article} {lemma}"
        else:
            # Languages without articles (Russian) show gender as a tag instead.
            gender_tag = lang_config.get("gender_tags", {}).get(gender)
            if gender_tag:
                tags.append(gender_tag)
        if morph.get("Number") == "Plur":
            tags.append("pl.")
        if lang_config.get("show_case"):
            case = CASE_LABELS.get(morph.get("Case", ""))
            if case:
                tags.append(case)

    elif token.pos_ == "VERB":
        conj_lemma = lemma
        # A reflexive particle sits on the end of the lemma and would
        # otherwise hide the conjugation class.
        for refl in lang_config.get("reflexive_suffixes", []):
            if conj_lemma.endswith(refl) and len(conj_lemma) > len(refl) + 2:
                conj_lemma = conj_lemma[: -len(refl)]
                tags.append("refl.")
                break
        if lang_config.get("show_aspect"):
            aspect = ASPECT_LABELS.get(morph.get("Aspect", ""))
            if aspect:
                tags.append(aspect)
        # Conjugation class from lemma ending
        for suffix in lang_config["verb_suffixes"]:
            if conj_lemma.endswith(suffix):
                tags.append(f"-{suffix}")
                break
        # Person and number
        person = PERSON_LABELS.get(morph.get("Person", ""), "")
        number = morph.get("Number", "")
        num_label = "sing." if number == "Sing" else "pl." if number == "Plur" else ""
        if person:
            tags.append(f"{person} {num_label}".strip())
        elif lang_config.get("gender_tags") and morph.get("Tense") == "Past":
            # The Russian past tense inflects for gender and number rather
            # than person.
            gender_tag = lang_config["gender_tags"].get(morph.get("Gender", ""), "")
            label = " ".join(x for x in (gender_tag, num_label) if x)
            if label:
                tags.append(label)
        # Tense and mood
        tense = morph.get("Tense", "")
        mood = morph.get("Mood", "")
        verb_form = morph.get("VerbForm", "")
        if verb_form == "Inf":
            tags.append("inf.")
        elif verb_form == "Ger":
            tags.append("gerund")
        elif verb_form == "Part":
            tags.append("participle")
        else:
            if tense:
                tense_map = lang_config.get("tense_map", TENSE_LABELS)
                tags.append(tense_map.get(tense, tense.lower()))
            if mood and mood != "Ind":
                tags.append(MOOD_LABELS.get(mood, mood.lower()))

    elif token.pos_ == "ADJ":
        if morph.get("Number") == "Plur":
            tags.append("pl.")
        if lang_config.get("show_case"):
            case = CASE_LABELS.get(morph.get("Case", ""))
            if case:
                tags.append(case)

    diminutive = match_diminutive(token, lang_config)
    if diminutive:
        tags.append(diminutive)

    tag_str = f", {', '.join(tags)}" if tags else ""
    return f"• <b>{token.text}</b> -> {lemma}{definition} <i>({token.pos_.lower()}{tag_str})</i>"


def build_vocab_html(doc, lang_config, lemma_translations=None):
    """The <br>-joined vocabulary list for one sentence."""
    bullets = [
        annotate_token(token, lang_config, lemma_translations)
        for token in doc
        if token.pos_ in VOCAB_POS
    ]
    # Identical bullets collapse; order is preserved.
    return "<br>".join(dict.fromkeys(bullets))


def format_card(study_sentence, known_sentence, audio_filename, vocab_html, front="study"):
    """The (front, back) pair for one card.

    The study language always carries the audio, whichever side it lands on.
    """
    audio_tag = f"[sound:{audio_filename}]"
    vocab_block = (
        f"<br><br><hr><br><b>Base Vocabulary:</b><br>{vocab_html}" if vocab_html else ""
    )
    if front == "english":
        # Recall direction: read the English, produce the study language.
        return known_sentence.strip(), f"{study_sentence} {audio_tag}{vocab_block}"

    front_of_card = f"{study_sentence} {audio_tag}"
    if vocab_html and known_sentence.strip():
        back_of_card = f"{known_sentence.strip()}{vocab_block}"
    elif vocab_html:
        back_of_card = f"<b>Base Vocabulary:</b><br>{vocab_html}"
    else:
        back_of_card = known_sentence.strip()
    return front_of_card, back_of_card


def create_anki_deck(input_filepath, tts_provider, audio_source=None, audio_padding=100, audio_offset=0, keep_annotations=False, no_cache=False, no_translate=False, detect_offset=False, translation_srt=None, source_lang="pt", input_lang=None, front="study", output_name=None, limit=None, merge_frags=True, keep_front_matter=False, rate_limit_wait=60.0, rate_limit_give_up=5, translator_name="auto", translator_delay=1.5):
    lang_config = LANGUAGE_CONFIGS[source_lang]
    input_lang = input_lang or source_lang
    # The input is already the study language unless it is English, in which
    # case the study language has to be produced by translating it.
    generated_study = input_lang != source_lang
    is_book = os.path.splitext(input_filepath)[1].lower() in BOOK_EXTENSIONS

    stem = os.path.splitext(os.path.basename(input_filepath))[0]
    # Book filenames are long and full of punctuation, and would otherwise
    # become the deck name, the audio directory and every mp3 filename.
    base_name = os.path.join(
        os.path.dirname(input_filepath),
        output_name or (slugify(stem) if is_book else stem),
    )
    safe_base_name = os.path.basename(base_name).replace(" ", "")
    output_filepath = f"{base_name}_AnkiDeck.tsv"
    audio_dir = f"{base_name}_Audio"

    os.makedirs(audio_dir, exist_ok=True)

    # Translations are cached to disk so a throttled or interrupted run can be
    # resumed without re-requesting sentences that already came back.
    cache_filepath = f"{base_name}_translations.json"
    translation_cache = {}
    if not no_cache and os.path.exists(cache_filepath):
        try:
            with open(cache_filepath, "r", encoding="utf-8") as cf:
                translation_cache = json.load(cf)
            print(f"Loaded {len(translation_cache)} cached translations")
        except (json.JSONDecodeError, OSError) as e:
            print(f"Could not read translation cache ({e}); starting fresh.")

    # Vocabulary definitions used to be re-requested on every batch of every
    # run, which dominates the cost of a resumed book run.
    lemma_cache_filepath = f"{base_name}_lemmas.json"
    lemma_cache = {}
    if not no_cache and os.path.exists(lemma_cache_filepath):
        try:
            with open(lemma_cache_filepath, "r", encoding="utf-8") as cf:
                lemma_cache = json.load(cf)
            print(f"Loaded {len(lemma_cache)} cached lemma definitions")
        except (json.JSONDecodeError, OSError) as e:
            print(f"Could not read lemma cache ({e}); starting fresh.")

    study_code = lang_config["translator_source"]
    # Book mode generates the study language from English; subtitle mode
    # translates the study language into English.
    if generated_study:
        translator = build_translator(
            translator_name, input_lang, study_code, lang_config, translator_delay
        )
    else:
        translator = build_translator(
            translator_name, study_code, "en", lang_config, translator_delay
        )
    # Vocabulary definitions always run study language -> English.
    lemma_translator = build_translator(
        translator_name, study_code, "en", lang_config, translator_delay
    )

    preloaded_translations = None
    source_audio = None
    all_srt_spans = []  # every span, unfiltered - used for offset detection

    if is_book:
        blocks = parse_book(input_filepath)
        print(f"Parsed {len(blocks)} blocks from {os.path.basename(input_filepath)}")
        texts = filter_book_blocks(blocks, keep_front_matter)
        if len(texts) != len(blocks):
            print(f"  {len(texts)} left after dropping front/back matter")
        if merge_frags:
            before = len(texts)
            texts = merge_fragments(texts)
            print(f"  {len(texts)} left after merging {before - len(texts)} sentence fragments")
        all_source_texts = texts
        all_timestamps = []
    else:
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(input_filepath, "r", encoding=encoding) as file:
                    content = file.read()
                print(f"Read subtitle file with encoding: {encoding}")
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            print("Error: Could not decode subtitle file with any supported encoding.")
            sys.exit(1)

        blocks = content.strip().split("\n\n")

        if translation_srt:
            preloaded_translations = parse_srt_texts(translation_srt, strip_annotations=not keep_annotations)
            print(f"Loaded {len(preloaded_translations)} translations from {translation_srt}")

        if audio_source:
            from pydub import AudioSegment
            print(f"Loading source audio: {audio_source}")
            source_audio = AudioSegment.from_file(audio_source)
            print(f"Audio loaded: {len(source_audio) / 1000:.1f}s")

        all_entries = []  # list of (text, timestamp_or_none)
        annotation_only = 0
        for block in blocks:
            # Collect the raw span before any filtering: offset detection wants every
            # timed event, including the sound effects we drop as cards.
            for line in block.split("\n"):
                if "-->" in line:
                    span = parse_srt_timestamp(line)
                    if span:
                        all_srt_spans.append(span)
                    break

            result = parse_subtitle_block(block, strip_annotations=not keep_annotations)
            if result:
                all_entries.append(result)
            elif parse_subtitle_block(block, strip_annotations=False):
                # Had content, but it was purely bracketed annotation.
                annotation_only += 1

        if annotation_only:
            print(f"Skipped {annotation_only} annotation-only blocks (sound effects, etc.)")

        all_source_texts = [e[0] for e in all_entries]
        all_timestamps = [e[1] for e in all_entries]

    if limit is not None:
        if limit <= 0:
            # Parse-only: report what the filters produced and stop before
            # spending anything on translation or audio.
            print(f"Parse-only run (--limit {limit}); nothing translated or written.")
            return
        all_source_texts = all_source_texts[:limit]
        print(f"Limited to the first {len(all_source_texts)} blocks (--limit)")

    print(f"Loading {source_lang} NLP model ({lang_config['spacy_model']})...")
    nlp = spacy.load(lang_config["spacy_model"])

    if detect_offset:
        if not source_audio:
            print("--detect-offset requires --audio; skipping detection.")
        elif not all_srt_spans:
            print("No SRT timestamps found; skipping offset detection.")
        else:
            print("Detecting audio offset...")
            detected, stats = detect_audio_offset(source_audio, all_srt_spans)
            if "reason" in stats:
                print(f"  Could not detect offset: {stats['reason']}")
            else:
                print(
                    f"  peak correlation {stats['corr']:.3f} (z={stats['z']:.1f}), "
                    f"halves agree to {stats['spread_ms']} ms {tuple(stats['halves'])}"
                )
            if detected is None:
                print(
                    f"  Low confidence - keeping --audio-offset {audio_offset} ms. "
                    "Verify by ear before a full run."
                )
            else:
                if audio_offset:
                    print(f"  Overriding --audio-offset {audio_offset} ms")
                audio_offset = detected
                print(f"  Using detected offset: {audio_offset:+d} ms")

    print(f"Starting processing of {len(all_source_texts)} blocks with audio generation...")

    anki_cards = []
    # Prose paragraphs survive the \n-join round trip less reliably than
    # subtitle lines, and each line-count mismatch costs a per-sentence retry.
    chunk_size = 20 if is_book else 40
    card_counter = 0
    failed_sentences = []
    limiter = RateLimiter(base_delay=rate_limit_wait, give_up_after=rate_limit_give_up)
    # Definitions get their own counter: being refused them must not push the
    # sentence path toward aborting the run.
    lemma_limiter = RateLimiter(base_delay=rate_limit_wait, give_up_after=rate_limit_give_up)
    # Vocabulary definitions are optional garnish; sentences are not. Being
    # throttled on definitions costs nothing but the definitions, so it stops
    # asking rather than stopping the run.
    skip_lemmas = False
    aborted = False
    # Books repeat lines, and distinct source blocks can land on the same
    # translation ("WHY" and "Why?" both become "Por que?"), which would give
    # two cards with identical backs and the same audio.
    seen_study = set()
    missing_audio = []

    for i in range(0, len(all_source_texts), chunk_size):
        chunk = all_source_texts[i : i + chunk_size]

        try:
            if preloaded_translations is not None:
                # Slice to len(chunk), not chunk_size: the final chunk is short,
                # and a longer translation SRT would otherwise fail the length
                # check and drop the whole last batch.
                en_texts = preloaded_translations[i : i + len(chunk)]
                if len(en_texts) < len(chunk):
                    print(
                        f"Translation SRT ran out after {len(preloaded_translations)} entries "
                        f"but the source has {len(all_source_texts)}; skipping remaining cards."
                    )
                    break
            elif no_translate:
                en_texts = [""] * len(chunk)
            else:
                missing = [t for t in chunk if t not in translation_cache]
                while missing:
                    # Only when generating the study language from English: that
                    # is where the benefit was measured, and lower-casing a
                    # capital in the source language risks flattening names.
                    prepared = (
                        [prepare_for_translation(m) for m in missing]
                        if generated_study else missing
                    )
                    # Cache keys stay the original text, not the prepared form.
                    for src, dst in zip(missing, translate_chunk(translator, prepared, limiter)):
                        if dst:
                            translation_cache[src] = dst
                    missing = [t for t in missing if t not in translation_cache]
                    # Only a rate-limit refusal is worth waiting on; anything
                    # else has already exhausted its own retries.
                    if not missing or not limiter.throttled:
                        break
                    if limiter.exhausted:
                        print(
                            f"\nStopping after {limiter.consecutive} consecutive rate-limit refusals. "
                            "Progress is cached; re-run the same command later to continue."
                        )
                        aborted = True
                        break
                    limiter.wait()
                en_texts = [translation_cache.get(t) for t in chunk]

            # The study language is what gets spacy, TTS and the vocab pass;
            # which side of the pair it is depends on the input language.
            if generated_study:
                study_texts, known_texts = en_texts, chunk
            else:
                study_texts, known_texts = chunk, en_texts

            if True:
                # NLP pass: batch-process all sentences and collect unique lemmas
                sentence_docs = list(nlp.pipe(
                    [normalize_case_for_nlp(t or "") for t in study_texts],
                    disable=["parser", "ner"],
                ))
                all_lemmas = list(dict.fromkeys(
                    token.lemma_
                    for doc in sentence_docs
                    for token in doc
                    if token.pos_ in VOCAB_POS
                ))

                lemma_translations = {}
                if all_lemmas and not no_translate and not skip_lemmas:
                    unknown = [l for l in all_lemmas if l not in lemma_cache]
                    while unknown:
                        time.sleep(1)
                        translated_lemmas = translate_with_retry(
                            lemma_translator, "\n".join(unknown), limiter=lemma_limiter
                        )
                        if translated_lemmas:
                            en_lemmas = translated_lemmas.split("\n")
                            if len(en_lemmas) == len(unknown):
                                lemma_cache.update(zip(unknown, en_lemmas))
                            else:
                                print("  Lemma line mismatch; vocab definitions omitted for this batch.")
                            break
                        if not lemma_limiter.throttled:
                            print("  Lemma translation failed; vocab definitions omitted for this batch.")
                            break
                        if lemma_limiter.exhausted:
                            skip_lemmas = True
                            print(
                                "  Rate limited on vocabulary definitions; skipping them for the "
                                "rest of this run. Cards are unaffected apart from the glosses."
                            )
                            break
                        lemma_limiter.wait()
                    lemma_translations = {l: lemma_cache[l] for l in all_lemmas if l in lemma_cache}

                for j, (study_sentence, known_sentence, doc) in enumerate(zip(study_texts, known_texts, sentence_docs)):
                    global_index = i + j

                    # No translation for this sentence: report it at the end
                    # instead of discarding the surrounding batch.
                    if study_sentence is None or known_sentence is None:
                        failed_sentences.append(chunk[j])
                        continue

                    if is_book:
                        if study_sentence in seen_study:
                            continue
                        seen_study.add(study_sentence)

                    card_counter += 1
                    if is_book:
                        # Books get hashed names rather than an index: changing
                        # the filter or fragment merging reshuffles positions,
                        # and an index-named clip would then be silently reused
                        # for different text. Hashing also dedupes repeats.
                        digest = hashlib.sha1(study_sentence.encode("utf-8")).hexdigest()[:10]
                        audio_filename = f"{safe_base_name}_{digest}.mp3"
                    else:
                        # Numbered by position in the SRT, not by card count, so a
                        # sentence keeps the same filename across resumed runs.
                        audio_filename = f"{safe_base_name}_{str(global_index + 1).zfill(4)}.mp3"
                    audio_filepath = os.path.join(audio_dir, audio_filename)

                    # 1. Generate Audio (skip if already exists)
                    if not os.path.exists(audio_filepath):
                        timestamp = all_timestamps[global_index] if global_index < len(all_timestamps) else None
                        if source_audio and timestamp:
                            try:
                                slice_audio(source_audio, timestamp[0], timestamp[1], audio_filepath, audio_padding, audio_offset)
                            except Exception as e:
                                print(f"Audio slicing failed for {audio_filename}: {e}")
                        else:
                            try:
                                generate_audio(study_sentence, audio_filepath, tts_provider, lang_config)
                            except Exception as e:
                                print(f"Audio generation failed for {audio_filename}: {e}")

                    # A [sound:] tag pointing at a file that was never written
                    # is a silently broken card in Anki, so track it.
                    if not os.path.exists(audio_filepath):
                        missing_audio.append(audio_filename)

                    # 2. Extract Base Vocabulary with English definitions
                    vocab_html = build_vocab_html(doc, lang_config, lemma_translations)

                    # 3. Format Card Sides
                    front_of_card, back_of_card = format_card(
                        study_sentence, known_sentence, audio_filename, vocab_html, front
                    )
                    anki_cards.append([front_of_card, back_of_card])

        except Exception as e:
            batch_start = i + 1
            batch_end = min(i + chunk_size, len(all_source_texts))
            print(f"Error processing cards {batch_start}-{batch_end}: {type(e).__name__}: {e}")

        print(f"Processed {min(i + chunk_size, len(all_source_texts))}/{len(all_source_texts)} cards...")

        # Persist after every batch so an interrupted run keeps its progress.
        if not no_cache and not no_translate and translation_cache:
            try:
                with open(cache_filepath, "w", encoding="utf-8") as cf:
                    json.dump(translation_cache, cf, ensure_ascii=False, indent=1)
            except OSError as e:
                print(f"Could not write translation cache: {e}")
        if not no_cache and not no_translate and lemma_cache:
            try:
                with open(lemma_cache_filepath, "w", encoding="utf-8") as cf:
                    json.dump(lemma_cache, cf, ensure_ascii=False, indent=1)
            except OSError as e:
                print(f"Could not write lemma cache: {e}")

        if not no_translate:
            time.sleep(1)

    # A run cut short by throttling holds only part of the deck. Overwriting a
    # larger existing deck with it would destroy finished work for no gain --
    # the caches already carry the progress into the next run.
    # "aborted" only covers breaking out of the loop; a run whose final batch was
    # refused ends throttled without ever reaching that check.
    if (aborted or limiter.throttled) and os.path.exists(output_filepath):
        try:
            with open(output_filepath, encoding="utf-8", newline="") as file:
                existing = sum(1 for _ in csv.reader(file, delimiter="\t"))
        except OSError:
            existing = 0
        if existing > len(anki_cards):
            print(
                f"\nKept the existing {existing}-card deck at {output_filepath} rather than "
                f"replacing it with this run's partial {len(anki_cards)}."
            )
            print("Caches are saved; re-run the same command to continue where this left off.")
            return

    if not anki_cards and (aborted or limiter.throttled):
        print("\nNo cards were produced. Nothing written; re-run later to retry.")
        return

    with open(output_filepath, "w", encoding="utf-8", newline="") as file:
        csv.writer(file, delimiter="\t").writerows(anki_cards)

    try:
        audio_on_disk = len([f for f in os.listdir(audio_dir) if f.endswith(".mp3")])
    except OSError:
        audio_on_disk = 0

    print(f"\nSaved {len(anki_cards)} cards to {output_filepath}")
    print(f"Audio files in {audio_dir}: {audio_on_disk}")

    if missing_audio:
        print(
            f"\n{len(missing_audio)} cards reference audio that was not written; "
            "they will play nothing in Anki."
        )
        for name in missing_audio[:5]:
            print(f"  - {name}")
        if len(missing_audio) > 5:
            print(f"  ... and {len(missing_audio) - 5} more")

    if no_translate:
        print(
            "Translation was skipped (--no-translate): card backs are empty and "
            "vocabulary has no English definitions."
        )
    if failed_sentences:
        print(
            f"\n{len(failed_sentences)} sentences had no translation and were skipped. "
            "Google's free endpoint throttles heavily; re-run the same command later "
            "and cached translations will be reused so only these are retried."
        )
        for t in failed_sentences[:5]:
            print(f"  - {t}")
        if len(failed_sentences) > 5:
            print(f"  ... and {len(failed_sentences) - 5} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert a subtitle file or ebook into an Anki flashcard deck.")
    parser.add_argument("input_file", help="Subtitle file (.srt, .vtt) or ebook (.epub, .mobi).")
    parser.add_argument(
        "--source-lang",
        choices=list(LANGUAGE_CONFIGS.keys()),
        default="pt",
        help="Language you are studying (default: pt). Supported: " + ", ".join(LANGUAGE_CONFIGS.keys()),
    )
    parser.add_argument(
        "--input-lang",
        default=None,
        help=(
            "Language of the input file, when it differs from --source-lang. "
            "Use 'en' for an English ebook: the study language is then produced "
            "by translating it, and gets the audio and vocabulary annotations."
        ),
    )
    parser.add_argument(
        "--front",
        choices=["study", "english"],
        default="study",
        help=(
            "Which side goes on the front of the card (default: study). "
            "'study' puts the study language + audio on the front; 'english' "
            "puts English on the front and the study language + audio on the back."
        ),
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help=(
            "Base name for the deck, audio directory and caches. Defaults to the "
            "input filename, slugified for ebooks (whose filenames are long)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Only process the first N blocks. Use this to test a book cheaply "
            "before a full run. --limit 0 parses and reports, then stops."
        ),
    )
    parser.add_argument(
        "--no-merge-fragments",
        action="store_true",
        help=(
            "Ebooks only. Keep every block as its own card instead of joining "
            "mid-sentence fragments (lines ending in a dash or comma) with the "
            "block that follows them."
        ),
    )
    parser.add_argument(
        "--keep-front-matter",
        action="store_true",
        help="Ebooks only. Keep the table of contents, copyright page and similar matter.",
    )
    parser.add_argument(
        "--translation-srt",
        default=None,
        help="SRT file containing pre-existing English translations. When provided, skips the translation API call.",
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="Source audio file (MP3, WAV, etc.) to extract per-card clips from using SRT timestamps.",
    )
    parser.add_argument(
        "--audio-padding",
        type=int,
        default=100,
        help="Padding in milliseconds around each extracted audio clip (default: 100).",
    )
    parser.add_argument(
        "--audio-offset",
        type=int,
        default=0,
        help=(
            "Offset in milliseconds to shift SRT timestamps when slicing audio. "
            "Positive values shift forward (audio starts later than SRT says), "
            "negative values shift backward (default: 0)."
        ),
    )
    parser.add_argument(
        "--detect-offset",
        action="store_true",
        help=(
            "Automatically estimate --audio-offset by cross-correlating audio "
            "energy against subtitle timings, and use the result. Requires --audio. "
            "Falls back to --audio-offset if the estimate is low-confidence."
        ),
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help=(
            "Skip translation entirely. Produces cards with source-language text + audio and an "
            "empty back. Makes no network requests, so it is fast and unthrottled - "
            "useful for checking --audio-offset alignment before committing to a full run."
        ),
    )
    parser.add_argument(
        "--translator",
        choices=["auto", "argos", "google", "mymemory"],
        default="auto",
        help=(
            "Translation backend (default: auto). 'auto' prefers the offline "
            "Argos model when its data is installed, then Google, then MyMemory. "
            "'argos' downloads the model if needed and never touches the network "
            "again; it uses Brazilian Portuguese, not European."
        ),
    )
    parser.add_argument(
        "--translator-delay",
        type=float,
        default=1.5,
        help="Minimum seconds between MyMemory requests, to stay within its free tier (default: 1.5).",
    )
    parser.add_argument(
        "--rate-limit-wait",
        type=float,
        default=60.0,
        help=(
            "Seconds to wait after the first rate-limit refusal, doubling each time "
            "up to 10 minutes (default: 60). The free Google endpoint sends no "
            "Retry-After header, so this is a guess rather than a published delay."
        ),
    )
    parser.add_argument(
        "--rate-limit-give-up",
        type=int,
        default=5,
        help=(
            "Stop after this many consecutive rate-limit refusals and let the caches "
            "carry progress to a later run (default: 5)."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore and do not write the *_translations.json cache.",
    )
    parser.add_argument(
        "--keep-annotations",
        action="store_true",
        help=(
            "Keep bracketed subtitle annotations such as [explosao distante] or "
            "[Sonic]. By default these are stripped, and blocks consisting only of "
            "annotations are skipped instead of becoming cards."
        ),
    )
    parser.add_argument(
        "--tts",
        choices=["gtts", "google-cloud", "azure", "polly", "elevenlabs"],
        default="gtts",
        help=(
            "TTS provider for audio generation when --audio is not used (default: gtts). "
            "google-cloud requires GOOGLE_APPLICATION_CREDENTIALS env var. "
            "azure requires AZURE_TTS_KEY and AZURE_TTS_REGION env vars. "
            "polly requires AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION); optionally POLLY_VOICE_ID (default Camila) and POLLY_SPEED (default slow). "
            "elevenlabs requires ELEVENLABS_API_KEY env var; optionally ELEVENLABS_VOICE_ID and ELEVENLABS_SPEED (0.7–1.2, default 0.7)."
        ),
    )
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print("Error: Input file not found.")
        sys.exit(1)

    is_book = os.path.splitext(args.input_file)[1].lower() in BOOK_EXTENSIONS
    generated_study = args.input_lang and args.input_lang != args.source_lang

    if generated_study and args.no_translate:
        # The study language IS the translation here, so skipping it leaves no
        # text to speak, annotate or put on a card.
        print("Error: --no-translate cannot be combined with --input-lang; the study language is the translation.")
        sys.exit(1)

    if is_book and args.audio:
        print("Error: --audio slices clips using subtitle timestamps and does not apply to ebooks.")
        sys.exit(1)

    if is_book and args.translation_srt:
        print("Error: --translation-srt pairs subtitle files and does not apply to ebooks.")
        sys.exit(1)

    if is_book and args.detect_offset:
        print("Error: --detect-offset aligns audio against subtitle timings and does not apply to ebooks.")
        sys.exit(1)

    if args.audio and not os.path.exists(args.audio):
        print("Error: Audio file not found.")
        sys.exit(1)

    if args.translation_srt and not os.path.exists(args.translation_srt):
        print("Error: Translation SRT file not found.")
        sys.exit(1)

    create_anki_deck(
        args.input_file,
        args.tts,
        audio_source=args.audio,
        audio_padding=args.audio_padding,
        audio_offset=args.audio_offset,
        keep_annotations=args.keep_annotations,
        no_cache=args.no_cache,
        no_translate=args.no_translate,
        detect_offset=args.detect_offset,
        translation_srt=args.translation_srt,
        source_lang=args.source_lang,
        input_lang=args.input_lang,
        front=args.front,
        output_name=args.output_name,
        limit=args.limit,
        merge_frags=not args.no_merge_fragments,
        keep_front_matter=args.keep_front_matter,
        rate_limit_wait=args.rate_limit_wait,
        rate_limit_give_up=args.rate_limit_give_up,
        translator_name=args.translator,
        translator_delay=args.translator_delay,
    )
