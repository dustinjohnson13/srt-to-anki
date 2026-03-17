# srt-to-anki

Converts SRT subtitle files into Anki flashcard decks with audio and vocabulary annotations. Designed for Portuguese → English language learning.

## Purpose

Takes a `.srt` subtitle file as input and produces:
- A `_AnkiDeck.tsv` file with Portuguese/English cards
- An `_Audio/` directory of numbered `.mp3` files (one per sentence)

Each card has:
- **Front:** Portuguese sentence + `[sound:file.mp3]` tag
- **Back:** English translation + annotated vocab list: nouns with gender articles (o/a), verbs with conjugation class (-ar/-er/-ir), tense, mood, and person, plural indicators, and diminutive/augmentative markers

## Setup

### Docker (recommended)

```bash
./run.sh <path-to-srt-file> --audio <path-to-audio-file>
```

The `run.sh` script builds a Docker image with all dependencies (Python 3.13, ffmpeg, spacy model) and runs the container, automatically mounting input files.

### Manual

```bash
python3.13 -m venv anki_stable
source anki_stable/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
```

Requires `ffmpeg` installed on the system if using `--audio` (e.g. `brew install ffmpeg`).

## Running

```bash
# Extract audio from source file (subs2srs-style)
python run.py <srt-file> --audio <audio-file>

# Extract audio with custom padding (default: 100ms)
python run.py <srt-file> --audio <audio-file> --audio-padding 200

# Apply an offset if audio and SRT timestamps are misaligned
python run.py <srt-file> --audio <audio-file> --audio-offset 2000   # audio starts 2s after SRT
python run.py <srt-file> --audio <audio-file> --audio-offset -1500  # audio starts 1.5s before SRT

# Use TTS instead (original behavior)
python run.py <srt-file> --tts gtts
```

## Project Structure

```
run.py              # Entire application (single file)
requirements.txt    # pip dependencies
Dockerfile          # Python 3.13-slim + ffmpeg + all deps
run.sh              # Docker build & run wrapper
.dockerignore       # Excludes venv, git, output files from build
README.md           # Setup instructions
anki_stable/        # Python 3.13 venv (do not modify)
```

## Key Implementation Details

- **Entry point:** `run.py` — single-file application
- **Venv:** `anki_stable/` using Python 3.13; activate before running
- **Docker:** `Dockerfile` + `run.sh` for containerized execution with all deps
- **Languages:** Hardcoded Portuguese source (`pt`), English target (`en`)
- **Audio modes:** `--audio` extracts clips from a source file using SRT timestamps; otherwise falls back to TTS
- **Audio padding:** `--audio-padding` (default 100ms) adds buffer around each extracted clip
- **Audio offset:** `--audio-offset` (default 0ms) shifts all SRT timestamps when slicing; positive = audio starts later, negative = earlier
- **Batch size:** 40 sentences per translation API call
- **Rate limiting:** `time.sleep(1)` between batches to avoid throttling
- **NLP model:** `pt_core_news_sm` (Spacy Portuguese Core News Small)
- **Vocab filter:** Only VERB, NOUN, ADJ, ADV tokens extracted
- **Deduplication:** `dict.fromkeys()` preserves order while deduplicating vocab

### `run.py` Functions

| Function | Purpose |
|---|---|
| `clean_text(text)` | Strips `[source:...]` tags, HTML tags, collapses whitespace |
| `parse_srt_timestamp(line)` | Extracts `(start_ms, end_ms)` from SRT timestamp line |
| `slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms)` | Extracts a clip from loaded audio with configurable padding |
| `generate_audio(text, filepath, provider)` | TTS audio generation via multiple providers |
| `create_anki_deck(input_filepath, tts_provider, audio_source, audio_padding)` | Main pipeline: parse → translate → NLP → audio → TSV |

## Dependencies

| Package | Purpose |
|---|---|
| `deep-translator` | Google Translate API wrapper |
| `spacy` | Portuguese NLP (POS tagging, lemmatization) |
| `gTTS` | Google Text-to-Speech MP3 generation |
| `pydub` | Audio slicing from source files (requires ffmpeg) |

## No Tests

No test suite exists. Manual testing is done by running with a sample `.srt` file and importing the resulting `.tsv` into Anki.

## Known Limitations / Areas for Improvement

- Languages are hardcoded (no CLI flags for source/target language)
- No progress bar for large files
- Silent error swallowing on failed audio/translation batches
- Line mismatch in a translation batch causes the entire chunk to be skipped
- No configuration file support
