import csv
import time
import argparse
import os
import sys
from deep_translator import GoogleTranslator
import spacy
from gtts import gTTS

def clean_text(text):
    tag_start = chr(91) + "source:"
    tag_end = chr(93)
    while tag_start in text:
        start_idx = text.find(tag_start)
        end_idx = text.find(tag_end, start_idx)
        if start_idx != -1 and end_idx != -1:
            text = text[:start_idx] + text[end_idx + 1:]
        else:
            break
            
    html_start = chr(60)
    html_end = chr(62)
    while html_start in text and html_end in text:
        start_idx = text.find(html_start)
        end_idx = text.find(html_end, start_idx)
        if start_idx != -1 and end_idx != -1:
            text = text[:start_idx] + text[end_idx + 1:]
        else:
            break
            
    text = text.replace(chr(10), " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
        
    return text

def create_anki_deck(input_filepath):
    # Setup paths and folders
    base_name = os.path.splitext(input_filepath)[0]
    safe_base_name = os.path.basename(base_name).replace(" ", "")
    output_filepath = base_name + "_AnkiDeck.tsv"
    audio_dir = base_name + "_Audio"
    
    if not os.path.exists(audio_dir):
        os.makedirs(audio_dir)

    with open(input_filepath, "r", encoding="utf-8") as file:
        content = file.read()

    blocks = content.strip().split(chr(10) + chr(10))
    translator = GoogleTranslator(source="pt", target="en")
    
    print("Loading Portuguese NLP dictionary...")
    nlp = spacy.load("pt_core_news_sm")
    
    all_pt_texts = []
    for block in blocks:
        lines = block.split(chr(10))
        if len(lines) >= 3:
            raw_text = chr(10).join(lines[2:])
            pt_text = clean_text(raw_text)
            if pt_text:
                all_pt_texts.append(pt_text)

    print("Starting processing of " + str(len(all_pt_texts)) + " blocks with audio generation...")

    anki_cards = []
    chunk_size = 40
    card_counter = 0
    
    for i in range(0, len(all_pt_texts), chunk_size):
        chunk = all_pt_texts[i : i + chunk_size]
        joined_chunk = chr(10).join(chunk)
        
        try:
            translated = translator.translate(joined_chunk)
            en_texts = translated.split(chr(10))
            
            if len(en_texts) == len(chunk):
                for j in range(len(chunk)):
                    pt_sentence = chunk[j]
                    en_sentence = en_texts[j].strip()
                    card_counter += 1
                    
                    # 1. Generate Audio
                    audio_filename = safe_base_name + "_" + str(card_counter).zfill(4) + ".mp3"
                    audio_filepath = os.path.join(audio_dir, audio_filename)
                    
                    try:
                        tts = gTTS(text=pt_sentence, lang="pt", slow=False)
                        tts.save(audio_filepath)
                    except Exception as e:
                        print("Audio generation failed for card " + str(card_counter) + ": " + str(e))
                    
                    # 2. Extract Base Vocabulary
                    doc = nlp(pt_sentence)
                    vocab_list = []
                    
                    for token in doc:
                        if token.pos_ in ["VERB", "NOUN", "ADJ", "ADV"]:
                            vocab_list.append("• " + chr(60) + "b" + chr(62) + token.text + chr(60) + "/b" + chr(62) + " -" + chr(62) + " " + token.lemma_ + " " + chr(60) + "i" + chr(62) + "(" + token.pos_.lower() + ")" + chr(60) + "/i" + chr(62))
                    
                    vocab_list = list(dict.fromkeys(vocab_list))
                    vocab_html = (chr(60) + "br" + chr(62)).join(vocab_list)
                    
                    # 3. Format Card Sides
                    # Front: Portuguese text + Anki sound tag
                    front_of_card = pt_sentence + " [sound:" + audio_filename + "]"
                    
                    # Back: English Translation + Vocab
                    if vocab_html:
                        back_of_card = en_sentence + chr(60) + "br" + chr(62) + chr(60) + "br" + chr(62) + chr(60) + "hr" + chr(62) + chr(60) + "br" + chr(62) + chr(60) + "b" + chr(62) + "Base Vocabulary:" + chr(60) + "/b" + chr(62) + chr(60) + "br" + chr(62) + vocab_html
                    else:
                        back_of_card = en_sentence
                        
                    anki_cards.append([front_of_card, back_of_card])
            else:
                print("Line mismatch in batch. Skipping chunk to prevent misaligned cards...")
                    
        except Exception as e:
            print("Error translating batch: " + str(e))
                
        print("Processed " + str(min(i + chunk_size, len(all_pt_texts))) + "/" + str(len(all_pt_texts)) + " cards...")
        time.sleep(1)

    with open(output_filepath, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter=chr(9))
        writer.writerows(anki_cards)
        
    print("Success! Saved TSV and generated " + str(card_counter) + " audio files.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("srt_file")
    args = parser.parse_args()

    if not os.path.exists(args.srt_file):
        print("Error: File not found.")
        sys.exit(1)
        
    create_anki_deck(args.srt_file)
