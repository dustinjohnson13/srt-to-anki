import csv
import json
import re
import time
import argparse
import os
import sys
from deep_translator import GoogleTranslator
import spacy
from gtts import gTTS

VOCAB_POS = {"VERB", "NOUN", "ADJ", "ADV"}

SRT_TIMESTAMP_RE = re.compile(
    r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})'
)

# Bracketed subtitle annotations: sound effects ([explosao distante]) and
# speaker/delivery tags ([Sonic], [ecoa]). Stripped by default; a block that is
# nothing but annotations is dropped rather than turned into a card.
ANNOTATION_RE = re.compile(r'\[[^\]]*\]')


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


def slice_audio(source_audio, start_ms, end_ms, output_path, padding_ms, offset_ms=0):
    actual_start = max(0, start_ms + offset_ms - padding_ms)
    actual_end = min(len(source_audio), end_ms + offset_ms + padding_ms)
    if actual_end <= actual_start:
        raise ValueError(f"Zero-duration clip: {actual_start}ms to {actual_end}ms")
    clip = source_audio[actual_start:actual_end]
    clip.export(output_path, format="mp3")


def generate_audio(text, filepath, provider):
    if provider == "gtts":
        gTTS(text=text, lang="pt", tld="com.br", slow=False).save(filepath)

    elif provider == "google-cloud":
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code="pt-BR",
                name="pt-BR-Neural2-A",
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
        speech_config.speech_synthesis_voice_name = "pt-BR-FranciscaNeural"
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
        voice_id = os.environ.get("POLLY_VOICE_ID", "Camila")
        speed = os.environ.get("POLLY_SPEED", "slow")
        response = client.synthesize_speech(
            Text=f"<speak><prosody rate=\"{speed}\">{text}</prosody></speak>",
            TextType="ssml",
            OutputFormat="mp3",
            VoiceId=voice_id,
            Engine="neural",
            LanguageCode="pt-BR",
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


def translate_with_retry(translator, text, tries=4, base_delay=2.0):
    """Translate one string, retrying with exponential backoff.

    The free Google endpoint used by deep-translator throttles aggressively and
    raises TranslationNotFound for arbitrary inputs when it does, so a single
    attempt is not a reliable signal of failure.
    """
    delay = base_delay
    for attempt in range(1, tries + 1):
        try:
            result = translator.translate(text)
            if result:
                return result
        except Exception:
            pass
        if attempt < tries:
            time.sleep(delay)
            delay *= 2
    return None


def translate_chunk(translator, chunk):
    """Translate a list of sentences, returning a same-length list.

    Fast path joins the chunk with newlines in one request. If that request
    fails or comes back with a different number of lines, fall back to
    per-sentence translation so one bad sentence costs one card instead of the
    whole batch. Untranslatable sentences come back as None.
    """
    joined = translate_with_retry(translator, "\n".join(chunk))
    if joined:
        lines = joined.split("\n")
        if len(lines) == len(chunk):
            return lines
        print(
            f"  Line mismatch (got {len(lines)}, expected {len(chunk)}); "
            "falling back to per-sentence translation..."
        )
    else:
        print("  Batch request failed; falling back to per-sentence translation...")

    results = []
    for sentence in chunk:
        results.append(translate_with_retry(translator, sentence, tries=3))
        time.sleep(0.3)
    return results


def create_anki_deck(input_filepath, tts_provider, audio_source=None, audio_padding=100, audio_offset=0, keep_annotations=False, no_cache=False, no_translate=False, detect_offset=False):
    base_name = os.path.splitext(input_filepath)[0]
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

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(input_filepath, "r", encoding=encoding) as file:
                content = file.read()
            print(f"Read SRT file with encoding: {encoding}")
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        print("Error: Could not decode SRT file with any supported encoding.")
        sys.exit(1)

    blocks = content.strip().split("\n\n")
    translator = GoogleTranslator(source="pt", target="en")

    print("Loading Portuguese NLP model...")
    nlp = spacy.load("pt_core_news_sm")

    source_audio = None
    if audio_source:
        from pydub import AudioSegment
        print(f"Loading source audio: {audio_source}")
        source_audio = AudioSegment.from_file(audio_source)
        print(f"Audio loaded: {len(source_audio) / 1000:.1f}s")

    all_entries = []  # list of (pt_text, timestamp_or_none)
    all_srt_spans = []  # every span, unfiltered - used for offset detection
    annotation_only = 0
    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 2:
            span = parse_srt_timestamp(lines[1])
            if span:
                all_srt_spans.append(span)
        if len(lines) >= 3:
            raw = "\n".join(lines[2:])
            pt_text = clean_text(raw, strip_annotations=not keep_annotations)
            if pt_text:
                timestamp = parse_srt_timestamp(lines[1]) if len(lines) >= 2 else None
                all_entries.append((pt_text, timestamp))
            elif clean_text(raw, strip_annotations=False):
                # Had content, but it was purely bracketed annotation.
                annotation_only += 1

    if annotation_only:
        print(f"Skipped {annotation_only} annotation-only blocks (sound effects, etc.)")

    all_pt_texts = [e[0] for e in all_entries]
    all_timestamps = [e[1] for e in all_entries]

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

    print(f"Starting processing of {len(all_pt_texts)} blocks with audio generation...")

    anki_cards = []
    chunk_size = 40
    card_counter = 0
    failed_sentences = []

    for i in range(0, len(all_pt_texts), chunk_size):
        chunk = all_pt_texts[i : i + chunk_size]

        try:
            if no_translate:
                en_texts = [""] * len(chunk)
            else:
                missing = [t for t in chunk if t not in translation_cache]
                if missing:
                    for src, dst in zip(missing, translate_chunk(translator, missing)):
                        if dst:
                            translation_cache[src] = dst
                en_texts = [translation_cache.get(t) for t in chunk]

            if True:
                # NLP pass: batch-process all sentences and collect unique lemmas
                sentence_docs = list(nlp.pipe(chunk, disable=["parser", "ner"]))
                all_lemmas = list(dict.fromkeys(
                    token.lemma_
                    for doc in sentence_docs
                    for token in doc
                    if token.pos_ in VOCAB_POS
                ))

                lemma_translations = {}
                if all_lemmas and not no_translate:
                    time.sleep(1)
                    translated_lemmas = translate_with_retry(translator, "\n".join(all_lemmas))
                    if translated_lemmas:
                        en_lemmas = translated_lemmas.split("\n")
                        if len(en_lemmas) == len(all_lemmas):
                            lemma_translations = dict(zip(all_lemmas, en_lemmas))
                        else:
                            print("  Lemma line mismatch; vocab definitions omitted for this batch.")
                    else:
                        print("  Lemma translation failed; vocab definitions omitted for this batch.")

                for j, (pt_sentence, en_sentence, doc) in enumerate(zip(chunk, en_texts, sentence_docs)):
                    global_index = i + j

                    # No translation for this sentence: report it at the end
                    # instead of discarding the surrounding batch.
                    if en_sentence is None:
                        failed_sentences.append(pt_sentence)
                        continue

                    card_counter += 1
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
                                print(f"Audio slicing failed for card {card_counter}: {e}")
                        else:
                            try:
                                generate_audio(pt_sentence, audio_filepath, tts_provider)
                            except Exception as e:
                                print(f"Audio generation failed for card {card_counter}: {e}")

                    # 2. Extract Base Vocabulary with English definitions
                    vocab_list = []
                    for token in doc:
                        if token.pos_ in VOCAB_POS:
                            en_def = lemma_translations.get(token.lemma_, "")
                            definition = f" ({en_def})" if en_def else ""
                            lemma = token.lemma_
                            morph = token.morph.to_dict()
                            tags = []

                            if token.pos_ == "NOUN":
                                gender = morph.get("Gender", "")
                                article = "o" if gender == "Masc" else "a" if gender == "Fem" else ""
                                if article:
                                    lemma = f"{article} {lemma}"
                                if morph.get("Number") == "Plur":
                                    tags.append("pl.")

                            elif token.pos_ == "VERB":
                                # Conjugation class from lemma ending
                                if lemma.endswith("ar"):
                                    tags.append("-ar")
                                elif lemma.endswith("er"):
                                    tags.append("-er")
                                elif lemma.endswith("ir"):
                                    tags.append("-ir")
                                # Person and number
                                person = morph.get("Person", "")
                                number = morph.get("Number", "")
                                if person:
                                    num_label = "sing." if number == "Sing" else "pl." if number == "Plur" else ""
                                    tags.append(f"{person}rd {num_label}".strip() if person == "3" else f"{person}{'st' if person == '1' else 'nd'} {num_label}".strip())
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
                                        tense_map = {"Pres": "present", "Past": "preterite", "Imp": "imperfect", "Fut": "future", "Pqp": "pluperfect"}
                                        tags.append(tense_map.get(tense, tense.lower()))
                                    if mood and mood != "Ind":
                                        mood_map = {"Sub": "subjunctive", "Imp": "imperative", "Cnd": "conditional"}
                                        tags.append(mood_map.get(mood, mood.lower()))

                            elif token.pos_ == "ADJ":
                                if morph.get("Number") == "Plur":
                                    tags.append("pl.")

                            # Diminutive/augmentative detection
                            word_lower = token.text.lower()
                            if word_lower != lemma.lower():
                                for suffix, label in [("inho", "dim."), ("inha", "dim."), ("inhos", "dim."), ("inhas", "dim."),
                                                      ("zinho", "dim."), ("zinha", "dim."), ("zinhos", "dim."), ("zinhas", "dim."),
                                                      ("ão", "aug."), ("ona", "aug."), ("ões", "aug."), ("onas", "aug."),
                                                      ("ito", "dim."), ("ita", "dim.")]:
                                    if word_lower.endswith(suffix):
                                        tags.append(label)
                                        break

                            tag_str = f", {', '.join(tags)}" if tags else ""
                            vocab_list.append(
                                f"• <b>{token.text}</b> -> {lemma}{definition} <i>({token.pos_.lower()}{tag_str})</i>"
                            )
                    vocab_html = "<br>".join(dict.fromkeys(vocab_list))

                    # 3. Format Card Sides
                    front_of_card = f"{pt_sentence} [sound:{audio_filename}]"
                    if vocab_html and en_sentence.strip():
                        back_of_card = f"{en_sentence.strip()}<br><br><hr><br><b>Base Vocabulary:</b><br>{vocab_html}"
                    elif vocab_html:
                        back_of_card = f"<b>Base Vocabulary:</b><br>{vocab_html}"
                    else:
                        back_of_card = en_sentence.strip()

                    anki_cards.append([front_of_card, back_of_card])

        except Exception as e:
            print(f"Error translating batch: {e}")

        print(f"Processed {min(i + chunk_size, len(all_pt_texts))}/{len(all_pt_texts)} cards...")

        # Persist after every batch so an interrupted run keeps its progress.
        if not no_cache and not no_translate and translation_cache:
            try:
                with open(cache_filepath, "w", encoding="utf-8") as cf:
                    json.dump(translation_cache, cf, ensure_ascii=False, indent=1)
            except OSError as e:
                print(f"Could not write translation cache: {e}")

        if not no_translate:
            time.sleep(1)

    with open(output_filepath, "w", encoding="utf-8", newline="") as file:
        csv.writer(file, delimiter="\t").writerows(anki_cards)

    print(f"\nSaved {len(anki_cards)} cards to {output_filepath}")
    print(f"Audio files in {audio_dir}: {card_counter}")

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
    parser = argparse.ArgumentParser(description="Convert an SRT subtitle file into an Anki flashcard deck.")
    parser.add_argument("srt_file")
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
            "Skip translation entirely. Produces cards with Portuguese + audio and an "
            "empty back. Makes no network requests, so it is fast and unthrottled - "
            "useful for checking --audio-offset alignment before committing to a full run."
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

    if not os.path.exists(args.srt_file):
        print("Error: SRT file not found.")
        sys.exit(1)

    if args.audio and not os.path.exists(args.audio):
        print("Error: Audio file not found.")
        sys.exit(1)

    create_anki_deck(
        args.srt_file,
        args.tts,
        audio_source=args.audio,
        audio_padding=args.audio_padding,
        audio_offset=args.audio_offset,
        keep_annotations=args.keep_annotations,
        no_cache=args.no_cache,
        no_translate=args.no_translate,
        detect_offset=args.detect_offset,
    )
