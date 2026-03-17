# Migration Plan: Audio Extraction + Docker

## Overview
Add two features:
1. Extract audio clips from a source MP3 using SRT timestamps (subs2srs-style)
2. Dockerize the app with all dependencies (Python 3.13, ffmpeg, spacy model)

Both TTS and audio-extraction paths coexist. `--audio <mp3>` triggers extraction; otherwise falls back to TTS.

---

## Implementation Steps

### 1. `requirements.txt` — Add `pydub`
- [x]Add `pydub` to the dependency list

### 2. `run.py` — SRT timestamp parsing
- [x]Add `parse_srt_timestamp(line)` function that extracts `(start_ms, end_ms)` from `HH:MM:SS,mmm --> HH:MM:SS,mmm`
- [x]Modify SRT parsing loop to collect timestamps alongside text into a list of `(pt_text, start_ms, end_ms)` tuples

### 3. `run.py` — Audio slicing function
- [x]Add `slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms)` function
- [x]Clamp padding: `max(0, start - padding)`, `min(len(audio), end + padding)`
- [x]Guard against zero/negative duration clips

### 4. `run.py` — CLI arguments
- [x]Add `--audio <path>` — optional path to source MP3
- [x]Add `--audio-padding <ms>` — integer, default 100
- [x]Validate `--audio` file exists if provided

### 5. `run.py` — Integration in `create_anki_deck`
- [x]Update function signature: `create_anki_deck(input_filepath, tts_provider, audio_source=None, audio_padding=100)`
- [x]Load source MP3 once before the main loop (if `audio_source` provided)
- [x]Route per-card audio: slice from source if available, else TTS
- [x]Track timestamps per card through the chunked processing loop

### 6. `Dockerfile`
- [x]Base: `python:3.13-slim`
- [x]Install `ffmpeg` via apt
- [x]Copy `requirements.txt`, run `pip install`, download spacy model
- [x]Copy `run.py`
- [x]`ENTRYPOINT ["python", "run.py"]`

### 7. `.dockerignore`
- [x]Exclude `anki_stable/`, `.git/`, `*.srt`, `*_Audio/`, `*_AnkiDeck.tsv`, `.DS_Store`

### 8. `run.sh` — Docker wrapper script
- [x]Build image (`srt-to-anki`)
- [x]Detect file args, mount parent directories into container
- [x]Handle SRT and MP3 in different directories (numbered mount points)
- [x]Forward TTS-related env vars (Azure, AWS, ElevenLabs, Google Cloud)
- [x]`chmod +x run.sh`

### 9. `CLAUDE.md` — Update documentation
- [x]Document new `--audio` and `--audio-padding` flags
- [x]Document Docker usage and `run.sh`
- [x]Add `pydub` and `ffmpeg` to dependencies table
- [x]Update project structure with new files

---

## Edge Cases

| Case | Handling |
|---|---|
| Padding before 0ms | Clamp to 0 |
| Padding past audio end | Clamp to `len(source_audio)` |
| SRT timestamp parse failure | Store `None`, fall back to TTS for that card, warn |
| MP3 shorter than SRT timestamps | Warn and skip if `start_ms > len(source_audio)` |
| Zero-duration clip | Skip with warning |
| `--audio` and `--tts` both given | Audio slicing takes priority for cards with valid timestamps |
| ffmpeg not installed (non-Docker) | pydub raises clear error |

---

## File Summary

| File | Action |
|---|---|
| `run.py` | Modify (steps 2–5) |
| `requirements.txt` | Modify (step 1) |
| `Dockerfile` | Create (step 6) |
| `.dockerignore` | Create (step 7) |
| `run.sh` | Create (step 8) |
| `CLAUDE.md` | Modify (step 9) |
