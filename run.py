import csv
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


def clean_text(text):
    text = re.sub(r'\[source:[^\]]*\]', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
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


def create_anki_deck(input_filepath, tts_provider, audio_source=None, audio_padding=100, audio_offset=0):
    base_name = os.path.splitext(input_filepath)[0]
    safe_base_name = os.path.basename(base_name).replace(" ", "")
    output_filepath = f"{base_name}_AnkiDeck.tsv"
    audio_dir = f"{base_name}_Audio"

    os.makedirs(audio_dir, exist_ok=True)

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
    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3:
            pt_text = clean_text("\n".join(lines[2:]))
            if pt_text:
                timestamp = parse_srt_timestamp(lines[1]) if len(lines) >= 2 else None
                all_entries.append((pt_text, timestamp))

    all_pt_texts = [e[0] for e in all_entries]
    all_timestamps = [e[1] for e in all_entries]

    print(f"Starting processing of {len(all_pt_texts)} blocks with audio generation...")

    anki_cards = []
    chunk_size = 40
    card_counter = 0

    for i in range(0, len(all_pt_texts), chunk_size):
        chunk = all_pt_texts[i : i + chunk_size]

        try:
            translated = translator.translate("\n".join(chunk))
            en_texts = translated.split("\n")

            if len(en_texts) != len(chunk):
                print("Line mismatch in batch. Skipping chunk to prevent misaligned cards...")
            else:
                # NLP pass: batch-process all sentences and collect unique lemmas
                sentence_docs = list(nlp.pipe(chunk, disable=["parser", "ner"]))
                all_lemmas = list(dict.fromkeys(
                    token.lemma_
                    for doc in sentence_docs
                    for token in doc
                    if token.pos_ in VOCAB_POS
                ))

                lemma_translations = {}
                if all_lemmas:
                    try:
                        time.sleep(1)
                        translated_lemmas = translator.translate("\n".join(all_lemmas))
                        en_lemmas = translated_lemmas.split("\n")
                        if len(en_lemmas) == len(all_lemmas):
                            lemma_translations = dict(zip(all_lemmas, en_lemmas))
                    except Exception as e:
                        print(f"Lemma translation failed: {e}")

                for j, (pt_sentence, en_sentence, doc) in enumerate(zip(chunk, en_texts, sentence_docs)):
                    card_counter += 1
                    global_index = i + j
                    audio_filename = f"{safe_base_name}_{str(card_counter).zfill(4)}.mp3"
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
                    if vocab_html:
                        back_of_card = f"{en_sentence.strip()}<br><br><hr><br><b>Base Vocabulary:</b><br>{vocab_html}"
                    else:
                        back_of_card = en_sentence.strip()

                    anki_cards.append([front_of_card, back_of_card])

        except Exception as e:
            print(f"Error translating batch: {e}")

        print(f"Processed {min(i + chunk_size, len(all_pt_texts))}/{len(all_pt_texts)} cards...")
        time.sleep(1)

    with open(output_filepath, "w", encoding="utf-8", newline="") as file:
        csv.writer(file, delimiter="\t").writerows(anki_cards)

    print(f"Success! Saved TSV and generated {card_counter} audio files.")


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

    create_anki_deck(args.srt_file, args.tts, audio_source=args.audio, audio_padding=args.audio_padding, audio_offset=args.audio_offset)
