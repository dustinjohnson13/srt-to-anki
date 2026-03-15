# srt-to-anki

Converts SRT subtitle files into Anki flashcard decks with audio and vocabulary annotations. Designed for Brazilian Portuguese → English language learning.

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
python run.py <path-to-srt-file> [--tts PROVIDER]
```

## Output

- `<name>_AnkiDeck.tsv` — import this into Anki
- `<name>_Audio/` — numbered `.mp3` files referenced by the cards

Each card has:
- **Front:** Brazilian Portuguese sentence + embedded audio
- **Back:** English translation + vocab list (verbs, nouns, adjectives, adverbs) with lemmas and POS tags

## TTS Providers

The `--tts` flag selects the audio generation backend. Default is **gtts**.

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
