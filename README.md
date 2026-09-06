# srt-to-anki

Converts SRT subtitle files into Anki flashcard decks with audio and vocabulary annotations. Designed for Brazilian Portuguese or French → English language learning.

## TL;DR

This command format works best:

```bash
./run.sh S01E01_PT-BR.srt --audio S01E01_PT-BR.mp3 --detect-offset --source-lang pt
```

`--source-lang pt` is required — the default is French. `--detect-offset` measures the alignment between the audio and the subtitles for you, so you don't have to guess `--audio-offset` by ear. Sound-effect-only subtitles (`[explosão distante]`) are skipped automatically, and speaker tags (`[Sonic]`) are stripped from the dialogue they precede.

Two things worth knowing:

- **Check the alignment first.** Add `--no-translate` for a fast pass that makes no network calls, then play a clip or two to confirm the audio lines up before committing to a full run:

  ```bash
  ./run.sh S01E01_PT-BR.srt --audio S01E01_PT-BR.mp3 --detect-offset --no-translate --source-lang pt
  ```

- **Expect to re-run.** Google's free translation endpoint throttles heavily, so a long episode often won't translate in one pass. Successful translations are cached to `<name>_translations.json`, so re-running the same command picks up where it left off instead of starting over. Already-generated audio clips are skipped too.

## Setup

### Docker (recommended)

No local setup needed — just Docker:

```bash
./run.sh <path-to-srt-file> --audio <path-to-audio-file>
```

The `run.sh` script builds an image with Python 3.13, ffmpeg, and all dependencies, then runs the container with your files automatically mounted.

### Manual

```bash
python3.13 -m venv anki_stable
source anki_stable/bin/activate
pip install -r requirements.txt
python -m spacy download pt_core_news_sm
```

Requires `ffmpeg` installed on the system if using `--audio` (e.g. `brew install ffmpeg`).

## Running

### Audio extraction (subs2srs-style)

Extract audio clips directly from a source audio file using SRT timestamps:

```bash
# Via Docker
./run.sh episode.srt --audio episode.mp3

# Manual
python run.py episode.srt --audio episode.mp3
```

#### Audio options

| Flag | Default | Description |
|---|---|---|
| `--audio <file>` | _(none)_ | Source audio file to extract clips from |
| `--audio-padding <ms>` | `100` | Padding in ms added before/after each clip |
| `--audio-offset <ms>` | `0` | Shift SRT timestamps to align with audio. Positive = audio starts later than SRT, negative = earlier |
| `--detect-offset` | off | Measure the offset automatically and use it. Requires `--audio`. Falls back to `--audio-offset` if the estimate is low-confidence |

```bash
# Custom padding (200ms buffer around each clip)
./run.sh episode.srt --audio episode.mp3 --audio-padding 200

# Offset if audio and subtitles are misaligned
./run.sh episode.srt --audio episode.mp3 --audio-offset 2000    # audio starts 2s after SRT
./run.sh episode.srt --audio episode.mp3 --audio-offset -1500   # audio starts 1.5s before SRT
```

#### Detecting the offset automatically

Rather than guessing `--audio-offset`, let the tool measure it:

```bash
./run.sh episode.srt --audio episode.mp3 --detect-offset
```

It builds a voice-activity signal from the audio and a second signal from the subtitle timings, then cross-correlates them to find the lag that lines them up:

```
Detecting audio offset...
  peak correlation 0.159 (z=10.4), halves agree to 10 ms (1960, 1950)
  Using detected offset: +1950 ms
```

The estimate is only used when it is confident — the correlation peak must stand well clear of the noise, and the offsets computed independently from each half of the file must agree. Otherwise it reports what it found and falls back to whatever `--audio-offset` you passed, rather than silently misaligning every clip.

#### Other options

| Flag | Default | Description |
|---|---|---|
| `--no-translate` | off | Skip translation entirely. Fast, no network calls. Cards get Portuguese + audio and an empty back — useful for checking alignment |
| `--keep-annotations` | off | Keep bracketed subtitle annotations (`[explosão distante]`, `[Sonic]`) instead of stripping them and skipping annotation-only lines |
| `--no-cache` | off | Ignore and do not write the `<name>_translations.json` translation cache |
| `--source-lang <lang>` | `fr` | Source language of the subtitles: `pt` or `fr` |
| `--translation-srt <file>` | _(none)_ | Use a subtitle file for the English side instead of calling the translation API. Avoids throttling entirely |

### TTS generation (no source audio)

If you don't have a source audio file, the tool generates speech via TTS:

```bash
python run.py episode.srt --tts gtts
```

## Output

- `<name>_AnkiDeck.tsv` — import this into Anki
- `<name>_Audio/` — numbered `.mp3` files referenced by the cards

Each card has:
- **Front:** Brazilian Portuguese sentence + embedded audio
- **Back:** English translation + annotated vocab list: nouns with gender articles (o/a), verbs with conjugation class (-ar/-er/-ir), tense, mood, and person, plural indicators, and diminutive/augmentative markers

## TTS Providers

The `--tts` flag selects the audio generation backend (used when `--audio` is not provided). Default is **gtts**.

| Provider | Flag | Quality | Cost | Requires | |
|---|---|---|---|---|---|
| gTTS | `gtts` | Basic | Free, no account | _(none)_ | **default** |
| ElevenLabs | `elevenlabs` | Excellent | Free tier (10k chars/mo) | `ELEVENLABS_API_KEY` | |
| Google Cloud TTS | `google-cloud` | Very good | Free tier (1M chars/mo) | `GOOGLE_APPLICATION_CREDENTIALS` | |
| Azure Cognitive Services | `azure` | Very good | Free tier (500k chars/mo) | `AZURE_TTS_KEY`, `AZURE_TTS_REGION` | |
| Amazon Polly | `polly` | Good | Free tier (5M chars/mo) | AWS credentials | |

### ElevenLabs

```bash
ELEVENLABS_API_KEY=your_key python run.py episode.srt --tts elevenlabs
```

Uses the `eleven_multilingual_v2` model for natural Brazilian Portuguese pronunciation. Optional env vars:

- `ELEVENLABS_VOICE_ID` — override the default voice (browse voices at elevenlabs.io/voice-library)
- `ELEVENLABS_SPEED` — playback speed from `0.7` to `1.2` (default `0.7`)

### Google Cloud TTS

```bash
GOOGLE_APPLICATION_CREDENTIALS=path/to/key.json python run.py episode.srt --tts google-cloud
```

Uses the `pt-BR-Neural2-A` neural voice.

### Azure Cognitive Services

```bash
AZURE_TTS_KEY=your_key AZURE_TTS_REGION=eastus python run.py episode.srt --tts azure
```

Uses the `pt-BR-FranciscaNeural` voice.

### Amazon Polly

```bash
AWS_ACCESS_KEY_ID=your_key AWS_SECRET_ACCESS_KEY=your_secret AWS_DEFAULT_REGION=us-east-1 python run.py episode.srt --tts polly
```

Uses the `Camila` neural voice (pt-BR) by default. Optional env vars:

- `POLLY_VOICE_ID` — override the voice (e.g. `Vitoria` for another female neural voice)
- `POLLY_SPEED` — SSML prosody rate, e.g. `slow`, `medium`, `fast`, or a percentage like `75%` (default `slow`)

### gTTS (fallback, no account needed)

```bash
python run.py episode.srt --tts gtts
```

Free with no setup, but noticeably more robotic than the other options.
