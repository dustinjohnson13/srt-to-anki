# srt-to-anki

Turns a subtitle file into an Anki deck: one card per line of dialogue, with audio clipped straight from the episode and a vocabulary breakdown on the back. Portuguese or French → English.

## TL;DR

```bash
./run.sh S01E01_PT-BR.srt --audio S01E01_PT-BR.mp3 --detect-offset
```

`--detect-offset` measures the audio/subtitle alignment for you, instead of guessing `--audio-offset` by ear. Sound-effect subtitles (`[explosão distante]`) are dropped, and speaker tags (`[Sonic]`) are stripped from the dialogue they precede.

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
```

Then use `python run.py` anywhere this README says `./run.sh`.

## Output

- `<name>_AnkiDeck.tsv` — the deck
- `<name>_Audio/` — one numbered `.mp3` per card

**Front:** source sentence + embedded audio.
**Back:** English translation, then a vocabulary list — nouns with gender articles, verbs with conjugation class, tense, mood and person, plus plural and diminutive/augmentative markers.

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
| `--source-lang <pt\|fr>` | `pt` | Subtitle language |
| `--translation-srt <file>` | — | Take the English side from this subtitle file instead of the API |
| `--no-translate` | off | Skip translation entirely. Fast, no network calls; card backs are empty |
| `--keep-annotations` | off | Keep `[explosão distante]` / `[Sonic]` instead of filtering them out |
| `--no-cache` | off | Ignore and don't write the translation cache |
| `--tts <provider>` | `gtts` | Voice used when `--audio` is absent (see below) |

### How offset detection works

It builds a voice-activity signal from the audio, a second one from the subtitle timings, and cross-correlates them to find the lag that lines them up:

```
Detecting audio offset...
  peak correlation 0.159 (z=10.4), halves agree to 10 ms (1960, 1950)
  Using detected offset: +1950 ms
```

The estimate is used only when it's confident: the correlation peak must stand clear of the noise, and the offsets computed independently from each half of the file must agree. Otherwise it reports what it found and falls back to `--audio-offset`, rather than silently misaligning every clip.

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

Optional overrides: `ELEVENLABS_VOICE_ID`, `ELEVENLABS_SPEED` (0.7–1.2, default 0.7), `POLLY_VOICE_ID`, `POLLY_SPEED` (`slow`/`medium`/`fast` or e.g. `75%`, default `slow`), `POLLY_LANGUAGE_CODE`.
