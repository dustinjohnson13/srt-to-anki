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


def clean_text(text):
    text = re.sub(r'\[source:[^\]]*\]', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def create_anki_deck(input_filepath):
    base_name = os.path.splitext(input_filepath)[0]
    safe_base_name = os.path.basename(base_name).replace(" ", "")
    output_filepath = f"{base_name}_AnkiDeck.tsv"
    audio_dir = f"{base_name}_Audio"

    os.makedirs(audio_dir, exist_ok=True)

    with open(input_filepath, "r", encoding="utf-8") as file:
        content = file.read()

    blocks = content.strip().split("\n\n")
    translator = GoogleTranslator(source="pt", target="en")

    print("Loading Portuguese NLP model...")
    nlp = spacy.load("pt_core_news_sm")

    all_pt_texts = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) >= 3:
            pt_text = clean_text("\n".join(lines[2:]))
            if pt_text:
                all_pt_texts.append(pt_text)

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

                for pt_sentence, en_sentence, doc in zip(chunk, en_texts, sentence_docs):
                    card_counter += 1
                    audio_filename = f"{safe_base_name}_{str(card_counter).zfill(4)}.mp3"
                    audio_filepath = os.path.join(audio_dir, audio_filename)

                    # 1. Generate Audio (skip if already exists)
                    if not os.path.exists(audio_filepath):
                        try:
                            gTTS(text=pt_sentence, lang="pt", slow=False).save(audio_filepath)
                        except Exception as e:
                            print(f"Audio generation failed for card {card_counter}: {e}")

                    # 2. Extract Base Vocabulary with English definitions
                    vocab_list = []
                    for token in doc:
                        if token.pos_ in VOCAB_POS:
                            en_def = lemma_translations.get(token.lemma_, "")
                            definition = f" ({en_def})" if en_def else ""
                            vocab_list.append(
                                f"• <b>{token.text}</b> -> {token.lemma_}{definition} <i>({token.pos_.lower()})</i>"
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
    args = parser.parse_args()

    if not os.path.exists(args.srt_file):
        print("Error: File not found.")
        sys.exit(1)

    create_anki_deck(args.srt_file)
