# srt-to-anki

Converts subtitle files into Anki flashcard decks with audio and vocabulary annotations. Portuguese or French → English.

Input: a `.srt` (or `.vtt`) subtitle file, optionally plus the episode audio.
Output: `<name>_AnkiDeck.tsv` and `<name>_Audio/` (one numbered `.mp3` per card).

Cards are source sentence + `[sound:...]` on the front; English translation plus an annotated vocab list on the back (gender articles, verb conjugation class, tense/mood/person, plural, diminutive/augmentative).

## Setup

**Docker** — `./run.sh <srt> --audio <audio>` builds the image (Python 3.13, ffmpeg, both spacy models) and mounts inputs automatically.

**Manual** — Python 3.13 plus `ffmpeg` on PATH:

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
python -m spacy download fr_core_news_sm
```

## Running

```bash
# Recommended first pass: detect the offset, skip translation, verify by ear
python run.py <srt> --audio <audio> --detect-offset --no-translate

# Full run (re-run to resume; the cache carries progress forward)
python run.py <srt> --audio <audio> --detect-offset

# Skip the API entirely using an English subtitle file
python run.py <srt> --audio <audio> --detect-offset --translation-srt <en-srt>

# No source audio: synthesize with TTS
python run.py <srt> --tts gtts
```

See README.md for the full flag table.

## Project Structure

```
run.py            # Entire application (single file)
run.sh            # Docker build & run wrapper
Dockerfile        # Python 3.13-slim + ffmpeg + pt/fr spacy models
requirements.txt  # pip dependencies
Portuguese/       # Standalone PT cheat sheets + their own generator scripts
README.md         # User-facing docs
```

## Key Implementation Details

- **Single file:** all logic lives in `run.py`; no package structure, no tests
- **Languages:** `--source-lang` (default `pt`) selects an entry from `LANGUAGE_CONFIGS`, which holds the spacy model, gTTS lang/tld, translator source, gender articles, verb suffixes, TTS voices, and diminutive/augmentative suffixes. Target is always English. Adding a language means adding one entry there plus the spacy model to the Dockerfile
- **Subtitle parsing:** `parse_subtitle_block()` locates the timestamp by scanning for `-->` rather than assuming a line index, so VTT (no cue ID, optional metadata) works alongside SRT
- **Annotation filter:** bracketed annotations are stripped by default. A block that is *only* annotation (`[explosao distante]`) is skipped; a tag prefixing real dialogue (`[Sonic] E tambem tem o Shadow.`) is removed and the dialogue kept, along with any dialogue dash the removal strands. `--keep-annotations` disables this
- **Audio modes:** `--audio` slices clips from a source file using subtitle timings; otherwise TTS synthesizes them. `--audio-padding` (default 100ms) buffers each clip; `--audio-offset` (default 0) shifts all timestamps, positive meaning the audio runs later
- **Offset detection:** `--detect-offset` builds a voice-activity signal from the audio (frame energy above a rolling median, which suppresses music beds), builds a second from the subtitle spans, and FFT cross-correlates them. Gated on peak z-score >= 5 **and** <= 1000ms disagreement between the two halves of the file; a low-confidence result falls back to `--audio-offset`. Uses every span including annotation-only ones, since more spans mean more signal
- **Translation sources,** in priority order: `--translation-srt` (a second subtitle file — both files are parsed with the same annotation policy so they drop the same blocks, but pairing is positional, so a file annotating different events will misalign), then `--no-translate` (empty backs), then the API
- **Translation resilience:** `translate_with_retry()` retries with exponential backoff; `translate_chunk()` falls back to per-sentence requests on failure or line mismatch, so one bad sentence costs one card rather than 40
- **Translation cache:** successful translations are written to `<name>_translations.json` after every batch and reused later, so a throttled run resumes. `--no-cache` disables it
- **Audio numbering:** clips are numbered by position in the filtered subtitle list, so a sentence keeps its filename across resumed runs and existing clips can be skipped
- **Batching:** 40 sentences per API call, `time.sleep(1)` between batches
- **Vocab:** only VERB/NOUN/ADJ/ADV tokens; `dict.fromkeys()` dedupes while preserving order

## `run.py` Functions

| Function | Purpose |
|---|---|
| `parse_subtitle_block(block, strip_annotations)` | Parses one SRT/VTT block → `(text, timestamp)`, or `None` if it has no timestamp or no text left |
| `parse_srt_texts(filepath, strip_annotations)` | Reads a whole subtitle file → list of cleaned texts (used for `--translation-srt`) |
| `clean_text(text, strip_annotations)` | Strips `[source:...]`, HTML, bracketed annotations and stranded dialogue dashes; collapses whitespace |
| `parse_srt_timestamp(line)` | `(start_ms, end_ms)` from a timestamp line |
| `slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms, offset_ms)` | Cuts one clip from loaded audio |
| `generate_audio(text, filepath, provider, lang_config)` | TTS synthesis across the five providers |
| `detect_audio_offset(source_audio, srt_spans, max_lag_ms, fps)` | Estimates the constant offset via VAD + FFT cross-correlation → `(offset_ms_or_None, stats)` |
| `translate_with_retry(translator, text, tries, base_delay)` | One translation with backoff retry → `None` on persistent failure |
| `translate_chunk(translator, chunk)` | Batch translate with per-sentence fallback → same-length list, `None` for failures |
| `create_anki_deck(...)` | Main pipeline: parse → filter → detect offset → translate → NLP → audio → TSV |

## Dependencies

| Package | Purpose |
|---|---|
| `deep-translator` | Google Translate wrapper (free endpoint; throttles) |
| `spacy` | POS tagging and lemmatization (`pt_core_news_sm`, `fr_core_news_sm`) |
| `pydub` | Audio slicing (requires ffmpeg) |
| `gTTS` | Default TTS |
| `google-cloud-texttospeech`, `azure-cognitiveservices-speech`, `boto3`, `elevenlabs` | Optional TTS providers |

`numpy` arrives via spacy and is imported lazily inside `detect_audio_offset()`.

## No Tests

No test suite. Manual testing: run against a sample `.srt` and import the `.tsv` into Anki.

## Known Limitations

- Google's free translation endpoint throttles aggressively; large decks usually need several runs, with the cache carrying progress forward
- `--translation-srt` pairing is positional, so mismatched annotation blocks between the two files shift the alignment
- No progress bar, no config file
