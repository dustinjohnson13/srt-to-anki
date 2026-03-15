# srt-to-anki

Converts SRT subtitle files into Anki flashcard decks with audio and vocabulary annotations. Designed for Portuguese → English language learning.

## Purpose

Takes a `.srt` subtitle file as input and produces:
- A `_AnkiDeck.tsv` file with Portuguese/English cards
- An `_Audio/` directory of numbered `.mp3` files (one per sentence)

Each card has:
- **Front:** Portuguese sentence + `[sound:file.mp3]` tag
- **Back:** English translation + a vocab list of verbs/nouns/adjectives/adverbs with lemmas and POS tags

## Setup

```bash
python3.13 -m venv anki_stable
source anki_stable/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
```

## Running

```bash
source anki_stable/bin/activate
python run.py <path-to-srt-file>
```

## Project Structure

```
run.py              # Entire application (single file)
requirements.txt    # pip dependencies
README.md           # Setup instructions
anki_stable/        # Python 3.13 venv (do not modify)
```

## Key Implementation Details

- **Entry point:** `run.py` — single-file application
- **Venv:** `anki_stable/` using Python 3.13; activate before running
- **Languages:** Hardcoded Portuguese source (`pt`), English target (`en`)
- **Batch size:** 40 sentences per translation API call
- **Rate limiting:** `time.sleep(1)` between batches to avoid throttling
- **NLP model:** `pt_core_news_sm` (Spacy Portuguese Core News Small)
- **Vocab filter:** Only VERB, NOUN, ADJ, ADV tokens extracted
- **Deduplication:** `dict.fromkeys()` preserves order while deduplicating vocab

### `run.py` Functions

| Function | Lines | Purpose |
|---|---|---|
| `clean_text(text)` | 10–35 | Strips `[source:...]` tags, HTML tags, collapses whitespace |
| `create_anki_deck(input_filepath)` | 37–131 | Main pipeline: parse → translate → NLP → audio → TSV |

## Dependencies

| Package | Purpose |
|---|---|
| `deep-translator` | Google Translate API wrapper |
| `spacy` | Portuguese NLP (POS tagging, lemmatization) |
| `gTTS` | Google Text-to-Speech MP3 generation |

## No Tests

No test suite exists. Manual testing is done by running with a sample `.srt` file and importing the resulting `.tsv` into Anki.

## Known Limitations / Areas for Improvement

- Languages are hardcoded (no CLI flags for source/target language)
- No progress bar for large files
- Silent error swallowing on failed audio/translation batches
- Line mismatch in a translation batch causes the entire chunk to be skipped
- No configuration file support
