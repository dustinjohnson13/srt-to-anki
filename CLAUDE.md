# srt-to-anki

Converts subtitle files and ebooks into Anki flashcard decks with audio and vocabulary annotations. Portuguese, French or Russian → English.

Input: a `.srt`/`.vtt` subtitle file (optionally plus the episode audio), or an `.epub`/`.mobi` ebook.
Output: `<name>_AnkiDeck.tsv` and `<name>_Audio/` (one `.mp3` per card).

Cards are source sentence + `[sound:...]` on the front; English translation plus an annotated vocab list on the back (gender articles, verb conjugation class, tense/mood/person, plural, diminutive/augmentative). `--front english` swaps the sides.

## Setup

**Docker** — `./run.sh <srt> --audio <audio>` builds the image (Python 3.13, ffmpeg, all three spacy models) and mounts inputs automatically.

**Manual** — Python 3.13 plus `ffmpeg` on PATH:

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
python -m spacy download fr_core_news_sm
python -m spacy download ru_core_news_sm
```

## Running

```bash
# Ebook: English in, Portuguese generated, English on the front.
# Always test with --limit before a full book.
python run.py <book.epub> --input-lang en --front english --limit 20

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
run.py                # Entire application (single file)
run.sh                # Docker build & run wrapper
Dockerfile            # Python 3.13-slim + ffmpeg + pt/fr/ru spacy models
requirements.txt      # runtime dependencies
requirements-dev.txt  # runtime + pytest
tests/                # pytest suite; conftest.py builds synthetic epub/mobi fixtures
Portuguese/           # Standalone PT cheat sheets + their own generator scripts
README.md             # User-facing docs
```

## Key Implementation Details

- **Single file:** all logic lives in `run.py`; no package structure. Tests live in `tests/` and import it as a top-level module (`conftest.py` puts the repo root on `sys.path`). Logic worth testing is pulled out into module-level functions rather than left inline in `create_anki_deck()`
- **Study vs known language:** the pipeline's central distinction. `study_texts` is the language being learned and always receives spacy, TTS and the vocab pass; `known_texts` is the plain English side. Which one is the *input* depends on `--input-lang`: normally the input is the study language and English is translated from it, but with `--input-lang en` the study language is generated from English instead. `generated_study` is the flag; binding these two names right after the translation branch is what lets one pipeline serve both directions
- **Ebook parsing:** stdlib only, no dependency. `parse_epub()` walks the OPF **spine** (zip order is arbitrary and would scramble the book); `parse_mobi()` does PDB record-table + PalmDOC decompression, stripping per-record trailing bytes indicated by the extra-data flags at `rec0[0xF2:0xF4]` — skip that and the decoder walks off the end of a record. Both funnel into `extract_html_blocks()`, an `HTMLParser` subclass that emits one string per block-level element
- **Book text cleanup:** `filter_book_blocks()` drops front/back matter by spine filename (`FRONT_MATTER_RE`) and by content (`BOILERPLATE_RE`); MOBI has no section names so only the content patterns apply and some front matter survives. `merge_fragments()` then joins blocks that don't end in terminal punctuation into the following block, because books break sentences across lines for rhythm and a fragment translated alone is nonsense. ALL-CAPS blocks are never merged — they're headings that stand alone
- **Book audio naming:** hashed from the study-language text rather than numbered by position. Book segmentation is not stable the way subtitle blocks are, so an index-named clip would be silently reused for different text after any change to filtering or merging. Hashing also dedupes repeated lines
- **`--limit`:** processes the first N blocks. A full book is thousands of throttled API calls, so always validate a slice first
- **Languages:** `--source-lang` (default `pt`) selects an entry from `LANGUAGE_CONFIGS`, which holds the spacy model, gTTS lang/tld, translator source, gender articles, verb suffixes, TTS voices, and diminutive/augmentative suffixes. Target is always English. Adding a language means adding one entry there plus the spacy model to the Dockerfile
- **Non-Romance annotation:** several `LANGUAGE_CONFIGS` keys are optional and absent for `pt`/`fr`, so the annotation pass falls back to the Romance behaviour unchanged. `gender_tags` shows noun gender as a tag when the language has no articles; `show_case` adds a case tag to nouns and adjectives; `show_aspect` adds `impf.`/`perf.` to verbs; `reflexive_suffixes` strips the reflexive particle off the lemma (and tags `refl.`) before the conjugation class is matched; `tense_map` overrides `TENSE_LABELS` (Russian `Past` is a plain past, not a preterite); `polly_engine` selects the Polly engine (Russian has no neural voice). Label maps (`TENSE_LABELS`, `MOOD_LABELS`, `CASE_LABELS`, `ASPECT_LABELS`, `PERSON_LABELS`) are module-level. `PERSON_LABELS` accepts both spellings because spacy reports Person as `1`/`2`/`3` for pt/fr but `First`/`Second`/`Third` for ru
- **Diminutives:** `diminutive_match` picks what the suffix list is tested against. `"surface"` (the default) requires the inflected form to differ from the lemma. Russian and Portuguese both set `"lemma"`, because their diminutives are separate lexemes whose base form equals the lemma — spacy does not relate `домик` to `дом` or `gatinho` to `gato`. Suffix lists are kept deliberately short: the productive suffixes also end ordinary words, and a wrong tag is worse than a missing one. Portuguese therefore detects **no augmentatives at all** (`-ão` ends every `-ção`/`-são` noun — it was tagging `não` as an augmentative 52 times in a single episode) and drops `-ito`/`-ita`; French detects nothing (`-ette`, `-et`, `-eau`, `-ot` end far more ordinary nouns than diminutives). `diminutive_exceptions` is a per-language set of ordinary words that end in a listed suffix (`caminho`, `linha`, `farinha`), checked against both the lemma and the surface form because the small models mislemmatise often enough to matter
- **Subtitle parsing:** `parse_subtitle_block()` locates the timestamp by scanning for `-->` rather than assuming a line index, so VTT (no cue ID, optional metadata) works alongside SRT
- **Annotation filter:** bracketed annotations are stripped by default. A block that is *only* annotation (`[explosao distante]`) is skipped; a tag prefixing real dialogue (`[Sonic] E tambem tem o Shadow.`) is removed and the dialogue kept, along with any dialogue dash the removal strands. `--keep-annotations` disables this
- **Audio modes:** `--audio` slices clips from a source file using subtitle timings; otherwise TTS synthesizes them. `--audio-padding` (default 100ms) buffers each clip; `--audio-offset` (default 0) shifts all timestamps, positive meaning the audio runs later
- **Offset detection:** `--detect-offset` builds a voice-activity signal from the audio (frame energy above a rolling median, which suppresses music beds), builds a second from the subtitle spans, and FFT cross-correlates them. Gated on peak z-score >= 5 **and** <= 1000ms disagreement between the two halves of the file; a low-confidence result falls back to `--audio-offset`. Uses every span including annotation-only ones, since more spans mean more signal
- **Translation sources,** in priority order: `--translation-srt` (a second subtitle file — both files are parsed with the same annotation policy so they drop the same blocks, but pairing is positional, so a file annotating different events will misalign), then `--no-translate` (empty backs), then the API
- **Translation resilience:** `translate_with_retry()` retries with exponential backoff; `translate_chunk()` falls back to per-sentence requests on failure or line mismatch, so one bad sentence costs one card rather than 40
- **Translation backends:** each service is a `TranslationBackend` subclass declaring how to address it (`config_key` naming its `LANGUAGE_CONFIGS` code, `english` for how it spells English) and how politely to use it (`min_interval`, `char_budget`, `max_query_chars`, `honours_delay`). `TRANSLATION_BACKENDS` is the registry and `AUTO_TRANSLATOR_ORDER` the `auto` chain, so adding a service (DeepL, Azure) is a class plus a registry entry, never another branch in `build_translator()`. `FallbackTranslator` moves to the next service permanently once one refuses; `ThrottledTranslator` enforces spacing, counts characters against the free allowance, and splits oversized requests on line boundaries
- **Offline translation:** `ArgosBackend` runs a local model — no network, no quota, nothing to throttle — and is first in `auto`. **Portuguese uses Argos's `pb` (Brazilian) model, not `pt` (European)**; they are separate models and `pt` produces *"Estás a ficar mais forte"* against `pb`'s *"Você está ficando mais forte"*, which would clash with the Brazilian TTS voices. Hence `argos_code` rather than reusing `translator_source`. `auto` uses the model only when already installed; naming `argos` explicitly downloads it (~80MB per direction)
- **MyMemory quirks:** it reports an exhausted quota **as the translated text** (`MYMEMORY WARNING: ...`), which `looks_like_quota_message()` catches so it is never cached as a definition, and it rejects any query over 500 characters, so `max_query_chars` splits batches on line boundaries to keep the response line count equal to the request's
- **Rate limiting:** the free Google endpoint answers a throttled request with a bare 429 — **no `Retry-After`, no `RateLimit-*` headers** (verified against the live endpoint), and deep-translator discards the response anyway, raising a `TooManyRequests` that carries only a static string. There is therefore no server-supplied delay to honour. `is_rate_limited()` classifies the refusal by exception type and message, and a rate-limited call returns immediately instead of burning its retries — retrying into an hour-long IP block wastes time and appears to prolong it. `RateLimiter` then waits and **retries the same batch**: escalating delay from `--rate-limit-wait` (default 60s), doubling, capped at 10 minutes, jittered ±20% so repeated runs don't retry in lockstep, giving up after `--rate-limit-give-up` consecutive refusals. Retrying the same batch rather than pausing before the next one matters — a run with a single batch (any small `--limit`) has no next batch, so a between-batches wait never executes at all. Sentences and definitions hold separate counters, both built once per run from the CLI flags, so a refusal on definitions cannot push the sentence path toward aborting. Sentences and definitions are treated differently on purpose: losing a **sentence** loses a card, so that aborts the run, while losing a **definition** only loses a gloss, so `skip_lemmas` stops asking for the rest of the run and the cards are still written. That distinction is what took a throttled 597-card rebuild from ~4 minutes of futile retrying to 17 seconds
- **Empty/partial deck guards:** a throttled run that produced no cards writes nothing at all, and a run that ends throttled will not overwrite an existing larger deck — it reports the card counts and leaves the finished file alone, since the caches already carry the progress into the next run. The check is `aborted or limiter.throttled`, because a run whose *final* batch is refused never reaches the loop's abort check
- **Translation cache:** successful translations are written to `<name>_translations.json` after every batch and reused later, so a throttled run resumes. Lemma definitions have their own `<name>_lemmas.json` — they were previously re-requested on every batch of every run, which dominated the cost of a resumed book run. `--no-cache` disables both
- **Audio numbering:** clips are numbered by position in the filtered subtitle list, so a sentence keeps its filename across resumed runs and existing clips can be skipped
- **Batching:** 40 sentences per API call (20 for books), `time.sleep(1)` between batches. `translate_chunk()` joins a batch with `\n` and needs the line count to survive the round trip; prose breaks that far more often than subtitle lines, and each mismatch costs a per-sentence retry pass
- **Vocab:** only VERB/NOUN/ADJ/ADV tokens; `dict.fromkeys()` dedupes while preserving order

## `run.py` Functions

| Function | Purpose |
|---|---|
| `parse_subtitle_block(block, strip_annotations)` | Parses one SRT/VTT block → `(text, timestamp)`, or `None` if it has no timestamp or no text left |
| `parse_srt_texts(filepath, strip_annotations)` | Reads a whole subtitle file → list of cleaned texts (used for `--translation-srt`) |
| `clean_text(text, strip_annotations)` | Strips `[source:...]`, HTML, bracketed annotations and stranded dialogue dashes; collapses whitespace |
| `parse_srt_timestamp(line)` | `(start_ms, end_ms)` from a timestamp line |
| `parse_book(filepath)` | Dispatches on extension → `parse_epub()` / `parse_mobi()` |
| `parse_epub(filepath)` | EPUB in spine order → `[(section_name, text)]` |
| `parse_mobi(filepath)` | MOBI/PalmDOC → `[(section_name, text)]` |
| `extract_html_blocks(html_text)` | Block-level text of an HTML fragment, in document order |
| `filter_book_blocks(blocks, keep_front_matter)` | Drops TOC/copyright/newsletter matter → list of texts |
| `merge_fragments(texts, max_chars)` | Joins mid-sentence fragments into whole sentences |
| `slugify(name, max_len)` | Filesystem-safe base name from a long book filename |
| `match_diminutive(token, lang_config)` | Diminutive/augmentative label for a token, or `None` |
| `annotate_token(token, lang_config, lemma_translations)` | One vocabulary bullet: surface form, lemma, gloss, grammar tags |
| `build_vocab_html(doc, lang_config, lemma_translations)` | The `<br>`-joined vocabulary list for one sentence |
| `format_card(study, known, audio_filename, vocab_html, front)` | The `(front, back)` pair for one card, in either direction |
| `is_rate_limited(exc)` | Whether an exception is the endpoint refusing us for rate reasons |
| `looks_like_quota_message(text)` | Whether a "translation" is really a service telling us to stop |
| `TranslationBackend` | Base strategy: how to address one service and how politely to use it |
| `ArgosBackend` / `GoogleBackend` / `MyMemoryBackend` | The registered services |
| `ArgosTranslator` | Local offline model, downloading on demand |
| `ThrottledTranslator` | Spacing, character budget, and oversized-query splitting |
| `FallbackTranslator` | Tries services in order, switching permanently on refusal |
| `build_translator(spec, source, target, lang_config, delay)` | Composes the chain for `auto` or a named service |
| `RateLimiter` | Escalating, jittered, capped backoff plus a give-up counter |
| `slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms, offset_ms)` | Cuts one clip from loaded audio |
| `generate_audio(text, filepath, provider, lang_config)` | TTS synthesis across the five providers |
| `detect_audio_offset(source_audio, srt_spans, max_lag_ms, fps)` | Estimates the constant offset via VAD + FFT cross-correlation → `(offset_ms_or_None, stats)` |
| `translate_with_retry(translator, text, tries, base_delay, limiter)` | One translation with backoff retry → `None` on persistent failure or a rate-limit refusal |
| `translate_chunk(translator, chunk, limiter)` | Batch translate with per-sentence fallback → same-length list, `None` for failures |
| `create_anki_deck(...)` | Main pipeline: parse → filter → detect offset → translate → NLP → audio → TSV |

## Dependencies

| Package | Purpose |
|---|---|
| `deep-translator` | Google Translate wrapper (free endpoint; throttles) |
| `spacy` | POS tagging and lemmatization (`pt_core_news_sm`, `fr_core_news_sm`, `ru_core_news_sm`) |
| `pydub` | Audio slicing (requires ffmpeg) |
| `gTTS` | Default TTS |
| `google-cloud-texttospeech`, `azure-cognitiveservices-speech`, `boto3`, `elevenlabs` | Optional TTS providers |
| `argostranslate` | Offline translation models (`ctranslate2`, `sentencepiece`, `stanza`); models download at runtime to `~/.local/share/argos-translate` |
| `pytest` | Test suite only, via `requirements-dev.txt` |

`numpy` arrives via spacy and is imported lazily inside `detect_audio_offset()`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                      # ~150 tests, about 1.5s
pytest tests/test_vocab.py  # one file
```

`tests/` covers the pure functions and the pipeline; the network is always stubbed.

| File | Covers |
|---|---|
| `test_ebook_parsing.py` | Block extraction, EPUB spine order, PalmDOC decompression, MOBI trailing bytes, format dispatch |
| `test_text_filters.py` | Front-matter filtering, fragment merging, slugs |
| `test_subtitles.py` | Timestamps, `clean_text`, SRT/VTT block parsing |
| `test_vocab.py` | Diminutive precision, gender articles, conjugation class, tense, `build_vocab_html` |
| `test_cards.py` | `format_card` in both directions |
| `test_translation.py` | Retry/backoff and the per-sentence fallback |
| `test_pipeline.py` | `create_anki_deck` end to end: deck shape, audio naming, caches, `--limit`, throttling behaviour |
| `test_rate_limiting.py` | 429 classification, backoff/jitter/cap, circuit breaker, no-retry-into-a-block |
| `test_translators.py` | Backend strategy and registry, per-service language codes, throttling, query splitting, quota-message detection, failover |

`tests/conftest.py` builds synthetic EPUB and MOBI files rather than committing binaries. The
EPUB fixture deliberately writes its documents to the zip in reverse spine order, so a
regression that reads zip order instead of spine order fails the test.

Tests needing spacy use the `pt_nlp` fixture and skip when the model is absent.

Beyond the suite: run against a real `.srt` or ebook and import the `.tsv` into Anki.

## Known Limitations

- Google's free translation endpoints currently refuse programmatic requests with 429 regardless of IP or user agent (verified across two networks); `--translator argos` sidesteps this entirely and is the default when its models are installed
- `--translation-srt` pairing is positional, so mismatched annotation blocks between the two files shift the alignment
- MOBI front matter is only partly filtered (no section names to go on); EPUB is clean
- Book cards inherit whatever the machine translation produces — fine for comprehension, but the generated side is not idiomatic native phrasing
- No progress bar, no config file
