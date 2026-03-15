#!/usr/bin/env python3
"""
Generate MP3 audio for all Portuguese example phrases in the cheat sheets.
Uses AWS Polly (Brazilian Portuguese, Camila neural voice).
Output goes to Portuguese/Audio/.

Requires AWS profile 'gh-dev' with Polly access.
"""

import os
import re
import time
import boto3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(SCRIPT_DIR, "Audio")

# Words that only (or almost only) appear in Portuguese, not English
PT_TOKENS = {
    'de', 'da', 'do', 'dos', 'das', 'em', 'no', 'na', 'nos', 'nas',
    'um', 'uma', 'uns', 'umas', 'não', 'por', 'para', 'pra',
    'com', 'sem', 'mas', 'sim', 'né', 'tá', 'são', 'ser', 'ter',
    'vai', 'vou', 'ela', 'ele', 'você', 'estou', 'está', 'isso',
    'aqui', 'como', 'onde', 'quando', 'que', 'quem', 'muito',
    'então', 'assim', 'esse', 'essa', 'este', 'esta', 'eles', 'elas',
    'nós', 'meu', 'minha', 'seu', 'sua', 'falar', 'comer', 'partir',
    'fazer', 'dizer', 'hoje', 'ontem', 'beleza', 'cara', 'tipo',
    'gente', 'legal', 'bacana', 'tudo', 'nada', 'cada', 'todo', 'toda',
    'sobre', 'desde', 'durante', 'entre', 'poder', 'querer', 'saber',
    'ver', 'vir', 'ficar', 'preciso', 'precisa', 'acho', 'deixa',
    'vida', 'tempo', 'lugar', 'casa', 'trabalho', 'dinheiro', 'comida',
    'nome', 'coisa', 'dia', 'ano', 'hora', 'vez', 'palavra', 'problema',
    # numbers
    'zero', 'um', 'dois', 'duas', 'três', 'quatro', 'cinco', 'seis', 'sete',
    'oito', 'nove', 'dez', 'onze', 'doze', 'treze', 'vinte', 'trinta',
    'quarenta', 'cem', 'mil',
    # days/months
    'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'feira', 'domingo',
    'janeiro', 'fevereiro', 'abril', 'maio', 'junho', 'julho',
    'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
    # articles — needed so "a coisa", "o tempo" etc. pass the 2-token rule
    'o', 'a', 'os', 'as',
    # other
    'mais', 'menos', 'também', 'talvez', 'bom', 'boa',
    'novo', 'nova', 'velho', 'velha', 'grande', 'pequeno', 'outro', 'outra',
    'mesmo', 'mesma', 'certo', 'certa', 'diferente',
    'obrigado', 'obrigada', 'socorro', 'ajuda',
    'posso', 'pode', 'vamos', 'sendo', 'tendo', 'pois', 'aí', 'vai',
    'chame', 'fica', 'fala', 'sou', 'foi', 'fomos', 'foram',
    'estive', 'esteve', 'tive', 'teve', 'fiz', 'fez', 'pude',
    'rolê', 'livraria', 'parente', 'biblioteca', 'oi',
}

PT_DIACRITICS = set('ãõçâêîôûàéíóúÃÕÇÂÊÎÔÛÀÉÍÓÚ')

# Strings that should never be spoken regardless of PT heuristics
BLOCKLIST = {
    'with ser', 'with estar', 'subject', 'category', 'tense',
    'english', 'portuguese', 'notes', 'translation', 'example',
    'use instead', 'irregular forms', 'superiority', 'equality',
    # single-letter/word table headers
    'ar', 'er', 'ir', 'eu', 'ser', 'ter', 'fazer', 'poder', 'estar', 'ir',
    'nós', 'vocês', 'você', 'eles', 'elas',
}


def is_portuguese(text):
    """Return True if text appears to be Portuguese rather than English."""
    if not text or len(text.strip()) < 2:
        return False
    lower = text.lower().strip()
    if lower in BLOCKLIST:
        return False
    # Has Portuguese-specific diacritics → always Portuguese
    if any(c in PT_DIACRITICS for c in text):
        return True
    words = set(re.findall(r'\b\w+\b', lower))
    # Single word: just needs to be a known PT word
    if len(words) == 1:
        return bool(words & PT_TOKENS)
    # Multi-word phrase: require 2+ PT tokens to filter out English bleed-through
    return len(words & PT_TOKENS) >= 2


def clean(text):
    """Normalize a raw string from markdown into plain speakable Portuguese."""
    # Strip bold/italic markers
    text = re.sub(r'\*+', '', text)
    # Strip inline code
    text = re.sub(r'`[^`]+`', '', text)
    # Strip leading/trailing pipes (table artifacts)
    text = text.strip('|').strip()
    # Strip parenthetical notes BEFORE slash-splitting so "(to do/make)" doesn't
    # leave unclosed fragments like "(to do" after the slash split
    text = re.sub(r'\([^)]*\)', '', text)
    # Replace placeholder ellipses (...) with nothing — they're filler, not content
    text = re.sub(r'\.{2,}', '', text)
    # For slash-separated variants, keep only the first: "X / Y" → "X"
    text = re.split(r'\s*/\s*', text)[0]
    # Strip "= English gloss": "Ele é mau = He is evil" → "Ele é mau"
    text = re.split(r'\s*=\s*[A-Za-z]', text)[0]
    # Strip "— English note": "X — a note about it" → "X"
    text = re.split(r'\s*—\s*[a-z]', text)[0]
    # Strip trailing label colons like "E aí?:"
    text = text.rstrip(':')
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(' .,;:-→')
    return text.strip()


def extract_italic(content):
    """Extract *single-asterisk* italic phrases, skipping inside **bold**."""
    pattern = re.compile(r'(?<!\*)\*([^*\n]{2,}?)\*(?!\*)')
    return [m.group(1).strip() for m in pattern.finditer(content)]


def extract_bold(content):
    """Extract **double-asterisk** bold phrases (e.g. slang terms in Bootstrap)."""
    pattern = re.compile(r'(?<!\*)\*\*([^*\n]+?)\*\*(?!\*)')
    return [m.group(1).strip() for m in pattern.finditer(content)]


def extract_pt_table_columns(content):
    """
    Extract cells only from table columns whose header identifies them as Portuguese.
    Matches headers containing 'portuguese', 'pt', or 'example'.
    Falls back to column 0 for tables with no recognized header (e.g. single-column
    vocabulary lists where Portuguese is always first).
    """
    results = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith('|'):
            i += 1
            continue

        # Parse header row
        raw_headers = [c.strip() for c in line.strip('|').split('|')]
        headers = [re.sub(r'\*+', '', h).strip().lower() for h in raw_headers]
        i += 1

        # Skip separator row (--- / :--: etc.)
        if i < len(lines) and re.match(r'^\|[\s|:-]+\|', lines[i].strip()):
            i += 1

        # Identify Portuguese columns by header name
        PT_HEADER_TERMS = {'portuguese', 'pt', 'example'}
        pt_cols = [j for j, h in enumerate(headers) if any(t in h for t in PT_HEADER_TERMS)]

        # If no header match, fall back to column 0 (most tables have PT first)
        if not pt_cols:
            pt_cols = [0]

        # Extract from identified columns for all data rows in this table
        while i < len(lines) and lines[i].strip().startswith('|'):
            row_cells = [c.strip() for c in lines[i].strip('|').split('|')]
            for j in pt_cols:
                if j < len(row_cells):
                    cell = row_cells[j].strip()
                    if not re.match(r'^[-:\s]+$', cell):
                        results.append(cell)
            i += 1

    return results


def collect_phrases(filepath):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    candidates = []

    # Single-asterisk italic phrases (inline examples throughout all files)
    candidates += extract_italic(content)

    # Double-asterisk bold terms (slang words, key terms in Bootstrap etc.)
    candidates += extract_bold(content)

    # Table cells from Portuguese-identified columns (header-aware)
    candidates += extract_pt_table_columns(content)

    # Deduplicate, clean, filter to Portuguese only
    seen = set()
    phrases = []
    for raw in candidates:
        cleaned = clean(raw)
        key = cleaned.lower()
        if cleaned and key not in seen and len(cleaned) >= 3 and is_portuguese(cleaned):
            seen.add(key)
            phrases.append(cleaned)

    return phrases


def generate_audio(text, filepath, polly_client):
    response = polly_client.synthesize_speech(
        Text=f'<speak><prosody rate="slow">{text}</prosody></speak>',
        TextType='ssml',
        OutputFormat='mp3',
        VoiceId='Camila',
        Engine='neural',
        LanguageCode='pt-BR',
    )
    with open(filepath, 'wb') as f:
        f.write(response['AudioStream'].read())


def main():
    os.makedirs(AUDIO_DIR, exist_ok=True)

    session = boto3.Session(profile_name='gh-dev')
    polly = session.client('polly', region_name='us-east-1')

    md_files = sorted(f for f in os.listdir(SCRIPT_DIR) if f.endswith('.md'))

    index_lines = ['# Audio Index\n']
    total_new = 0
    total_skip = 0

    for md_file in md_files:
        stem = os.path.splitext(md_file)[0]
        filepath = os.path.join(SCRIPT_DIR, md_file)
        phrases = collect_phrases(filepath)

        print(f'\n{md_file}: {len(phrases)} phrases')
        index_lines.append(f'\n## {md_file}\n')

        for i, phrase in enumerate(phrases, 1):
            filename = f'{stem}_{i:03d}.mp3'
            out_path = os.path.join(AUDIO_DIR, filename)
            index_lines.append(f'- `{filename}` — {phrase}')

            if os.path.exists(out_path):
                print(f'  [skip] {filename}')
                total_skip += 1
                continue

            print(f'  [{i:03d}] {phrase[:70]}')
            try:
                generate_audio(phrase, out_path, polly)
                total_new += 1
                time.sleep(0.1)
            except Exception as e:
                print(f'    ERROR: {e}')

    index_path = os.path.join(AUDIO_DIR, 'INDEX.md')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))

    print(f'\nDone. Generated {total_new} new files, skipped {total_skip} existing.')
    print(f'Index written to {index_path}')


if __name__ == '__main__':
    main()
