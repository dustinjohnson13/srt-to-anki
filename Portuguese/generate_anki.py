#!/usr/bin/env python3
"""
Generate an Anki flashcard deck from the Portuguese cheat sheet audio index.

Reads Portuguese/Audio/INDEX.md, translates all phrases to English,
and writes Portuguese_Cheatsheets_AnkiDeck.tsv.

Card format:
  Front: Portuguese phrase  [sound:filename.mp3]
  Back:  English translation
"""

import csv
import re
import time
import os
from deep_translator import GoogleTranslator

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(SCRIPT_DIR, "Audio", "INDEX.md")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "Portuguese_Cheatsheets_AnkiDeck.tsv")

# Phrases that are structural artifacts rather than translatable content
# (mathematical/symbolic expressions that the translator will mangle)
SKIP_PHRASES = {
    'De + o', 'Em + o', 'Por + o', 'para + o',
    'o, os', 'a, as', 'um, uns', 'uma, umas',
}

CHUNK_SIZE = 40


def parse_index(path):
    """Return list of (filename, phrase) from INDEX.md."""
    entries = []
    pattern = re.compile(r'^- `([^`]+)` — (.+)$')
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = pattern.match(line.strip())
            if m:
                filename, phrase = m.group(1), m.group(2).strip()
                if phrase not in SKIP_PHRASES:
                    entries.append((filename, phrase))
    return entries


def translate_batch(phrases, translator):
    """Translate a list of phrases as a single newline-joined request."""
    joined = '\n'.join(phrases)
    result = translator.translate(joined)
    lines = result.split('\n')
    if len(lines) == len(phrases):
        return lines
    # Mismatch: fall back to individual translations
    print(f"  Warning: batch size mismatch ({len(phrases)} sent, {len(lines)} received). Falling back to individual.")
    translations = []
    for phrase in phrases:
        try:
            translations.append(translator.translate(phrase))
            time.sleep(0.2)
        except Exception as e:
            print(f"  Error translating '{phrase}': {e}")
            translations.append('')
    return translations


def main():
    entries = parse_index(INDEX_PATH)
    print(f"Loaded {len(entries)} phrases from index.")

    translator = GoogleTranslator(source='pt', target='en')
    phrases = [phrase for _, phrase in entries]
    translations = []

    for i in range(0, len(phrases), CHUNK_SIZE):
        chunk = phrases[i:i + CHUNK_SIZE]
        print(f"Translating phrases {i+1}–{min(i+CHUNK_SIZE, len(phrases))}...")
        try:
            batch_result = translate_batch(chunk, translator)
            translations.extend(batch_result)
        except Exception as e:
            print(f"  Batch error: {e}. Skipping chunk.")
            translations.extend([''] * len(chunk))
        time.sleep(1)

    cards = []
    for (filename, phrase), english in zip(entries, translations):
        front = f"{phrase} [sound:{filename}]"
        back = english.strip() if english else '(translation unavailable)'
        cards.append([front, back])

    with open(OUTPUT_PATH, 'w', encoding='utf-8', newline='') as f:
        csv.writer(f, delimiter='\t').writerows(cards)

    print(f"\nWrote {len(cards)} cards to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
