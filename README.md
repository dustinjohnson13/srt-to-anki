# srt-to-anki

Turns a subtitle file or an ebook into an Anki deck, with audio and a vocabulary breakdown on every card. Portuguese, French or Russian → English.

- **Subtitles** — one card per line of dialogue, audio clipped straight from the episode.
- **Ebooks** (`.epub`, `.mobi`) — one card per paragraph, audio synthesized with TTS.

## TL;DR

```bash
./run.sh S01E01_PT-BR.srt --audio S01E01_PT-BR.mp3 --detect-offset
```

`--detect-offset` measures the audio/subtitle alignment for you, instead of guessing `--audio-offset` by ear. Sound-effect subtitles (`[explosão distante]`) are dropped, and speaker tags (`[Sonic]`) are stripped from the dialogue they precede.

For a book, pointing at an **English** ebook and generating the Portuguese gives you recall-direction cards — English on the front, Portuguese plus audio on the back:

```bash
./run.sh book.epub --input-lang en --front english --limit 20
```

Always start with `--limit`: a full book is thousands of translations, and you want to see twenty cards before committing to that.

Two things worth knowing:

- **Check the alignment first.** Add `--no-translate` for a fast pass that makes no network calls, then play a clip or two before committing to a full run.
- **Expect to re-run.** Google's free translation endpoint throttles heavily, so a long episode rarely finishes in one pass. Translations are cached to `<name>_translations.json` and existing audio clips are skipped, so re-running the same command resumes instead of restarting. If you have an English subtitle file, `--translation-srt` avoids the API altogether.

## Setup

**Docker (recommended).** Nothing to install but Docker — `run.sh` builds the image and mounts your files automatically.

**Manual.** Needs Python 3.13 and `ffmpeg` on PATH (`brew install ffmpeg`):

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
python -m spacy download fr_core_news_sm   # only for --source-lang fr
python -m spacy download ru_core_news_sm   # only for --source-lang ru
```

Then use `python run.py` anywhere this README says `./run.sh`.

## Output

- `<name>_AnkiDeck.tsv` — the deck
- `<name>_Audio/` — one `.mp3` per card

`<name>` is the input filename, or a slug of it for ebooks (whose filenames are long); override with `--output-name`.

**Front:** source sentence + embedded audio.
**Back:** English translation, then a vocabulary list — nouns with gender articles, verbs with conjugation class, tense, mood and person, plus plural and diminutive/augmentative markers.

`--front english` swaps the sides: English on the front, and the language you are studying — plus its audio and vocabulary — on the back.

### Books

The language you are studying always gets the audio and the vocabulary annotations, whether it came from the file or was generated:

| | Front | Back |
|---|---|---|
| Portuguese ebook | Portuguese + audio | English + vocabulary |
| English ebook, `--input-lang en --front english` | English | Portuguese + audio + vocabulary |

Before translating, the text is cleaned up in two passes. The table of contents, copyright page and similar matter are dropped (`--keep-front-matter` keeps them), and mid-sentence fragments are joined with the block that follows. That second pass matters: books break sentences across lines for rhythm, and `To push back—` translated on its own is nonsense. ALL-CAPS headings are left alone, since they stand by themselves. Use `--no-merge-fragments` for one card per block regardless.

EPUB is read in spine order, so cards follow reading order rather than zip order. MOBI has no section names, so front matter is caught only by content patterns and some of it will survive into the deck.

Book audio is named by a hash of its text rather than by position. Re-running after a change to the filtering or merging then reuses the clips it should and regenerates the rest, and repeated lines share a single file.

### Importing into Anki

1. Copy the contents of `<name>_Audio/` into your Anki media folder (*Tools → Check Media → View Files*).
2. *File → Import*, pick the `.tsv`, set the separator to **Tab**, and tick **Allow HTML in fields** — otherwise the vocabulary list renders as literal `<br>` tags.

## Options

| Flag | Default | Description |
|---|---|---|
| `--audio <file>` | — | Cut clips from this audio file using the subtitle timings |
| `--detect-offset` | off | Measure the audio/subtitle offset and use it. Requires `--audio` |
| `--audio-offset <ms>` | `0` | Set the offset by hand. Positive = audio runs later than the subtitles |
| `--audio-padding <ms>` | `100` | Buffer added to each end of a clip |
| `--source-lang <pt\|fr\|ru>` | `pt` | The language you are studying |
| `--input-lang <code>` | = `--source-lang` | Language of the input file when it differs. `en` generates the study language by translating |
| `--front <study\|english>` | `study` | Which side the front of the card shows |
| `--output-name <name>` | from filename | Base name for the deck, audio directory and caches |
| `--limit <n>` | — | Only process the first N blocks. Use this to test a book before a full run |
| `--no-merge-fragments` | off | Ebooks: keep every block as its own card instead of joining mid-sentence fragments |
| `--keep-front-matter` | off | Ebooks: keep the table of contents, copyright page and similar matter |
| `--translation-srt <file>` | — | Take the English side from this subtitle file instead of the API |
| `--no-translate` | off | Skip translation entirely. Fast, no network calls; card backs are empty |
| `--keep-annotations` | off | Keep `[explosão distante]` / `[Sonic]` instead of filtering them out |
| `--no-cache` | off | Ignore and don't write the translation and lemma caches |
| `--rate-limit-wait <s>` | `60` | Wait after the first rate-limit refusal, doubling each time, capped at 10 min |
| `--rate-limit-give-up <n>` | `5` | Stop after this many consecutive refusals and leave the rest to a later run |
| `--tts <provider>` | `gtts` | Voice used when `--audio` is absent (see below) |

### Vocabulary annotations

The back of each card lists every verb, noun, adjective and adverb with its lemma, an English gloss, and the grammar that applies to the language:

| | Portuguese / French | Russian |
|---|---|---|
| Nouns | gender as an article (`o carro`, `la maison`), plural | gender as a tag (`m.`/`f.`/`n.`), plural, **case** |
| Adjectives | plural | plural, **case** |
| Verbs | conjugation class, person, number, tense, mood | **aspect** (`impf.`/`perf.`), **reflexive**, conjugation class, person/number (gender in the past), tense, mood |

```
• собакой -> собака (noun, f., instr.)
• написал -> написать (verb, perf., -ать, m. sing., past)
• двигайся -> двигаться (verb, refl., impf., -ать, 2nd sing., imperative)
```

Diminutives are flagged only where the suffix is unambiguous, because a wrong tag is worse than a missing one. Every list is deliberately short — the productive suffixes in all three languages also end perfectly ordinary words.

- **Portuguese** flags `-inho`/`-inha` and `-zinho`/`-zinha` (`gatinho`, `cafezinho`), minus an exception list for ordinary words with those endings (`caminho`, `linha`, `farinha`). **Augmentatives are not detected at all**: `-ão` ends every `-ção` and `-são` noun, so `coração` and even `não` were being reported as augmentatives of nothing.
- **French** detects none. `-ette`, `-et`, `-eau` and `-ot` end far more ordinary nouns (`recette`, `objet`, `bureau`, `mot`) than diminutives, and modern French barely forms them.
- **Russian** flags `-енька`, `-онька`, `-ечко` and `-ышко`; `-ик`, `-ок` and `-ище` end ordinary nouns as often as diminutive ones (`ребёнок`, `чудовище`).

### How offset detection works

It builds a voice-activity signal from the audio, a second one from the subtitle timings, and cross-correlates them to find the lag that lines them up:

```
Detecting audio offset...
  peak correlation 0.159 (z=10.4), halves agree to 10 ms (1960, 1950)
  Using detected offset: +1950 ms
```

The estimate is used only when it's confident: the correlation peak must stand clear of the noise, and the offsets computed independently from each half of the file must agree. Otherwise it reports what it found and falls back to `--audio-offset`, rather than silently misaligning every clip.

### When Google throttles you

The free endpoint returns a bare `429` with no `Retry-After` and no rate-limit headers, so there is nothing to read that says when to come back. The script therefore paces itself: a refusal is recognised immediately rather than retried into (which only prolongs the block), the next batch waits 60s, then 120s, 240s and so on up to 10 minutes with a little jitter, and after five consecutive refusals it stops and tells you to re-run later.

Vocabulary definitions are treated separately from sentences. Losing a sentence loses a card, so that ends the run; losing a definition only loses the English gloss, so it stops asking and still writes the deck. And a run that ends throttled will never overwrite a larger existing deck with a partial one.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Around 150 tests, roughly 1.5 seconds, no network. Tests that need the Portuguese spacy model skip themselves when it is not installed.

## TTS providers

Used only when `--audio` isn't given. Per-language voices live in `LANGUAGE_CONFIGS` in `run.py`.

| Provider | Flag | Quality | Free tier | Requires |
|---|---|---|---|---|
| gTTS *(default)* | `gtts` | Basic | Unlimited, no account | — |
| ElevenLabs | `elevenlabs` | Excellent | 10k chars/mo | `ELEVENLABS_API_KEY` |
| Google Cloud | `google-cloud` | Very good | 1M chars/mo | `GOOGLE_APPLICATION_CREDENTIALS` |
| Azure | `azure` | Very good | 500k chars/mo | `AZURE_TTS_KEY`, `AZURE_TTS_REGION` |
| Amazon Polly | `polly` | Good | 5M chars/mo | AWS credentials |

```bash
ELEVENLABS_API_KEY=your_key python run.py episode.srt --tts elevenlabs
```

Optional overrides: `ELEVENLABS_VOICE_ID`, `ELEVENLABS_SPEED` (0.7–1.2, default 0.7), `POLLY_VOICE_ID`, `POLLY_SPEED` (`slow`/`medium`/`fast` or e.g. `75%`, default `slow`), `POLLY_LANGUAGE_CODE`, `POLLY_ENGINE`.

Polly has no neural Russian voice, so `--source-lang ru --tts polly` uses the standard engine (Tatyana) automatically.
