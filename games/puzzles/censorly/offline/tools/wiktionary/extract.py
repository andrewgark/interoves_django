"""Extract Russian morpheme segmentations and root nests from ru.wiktionary.

Offline, stdlib only, no Django: the dump is untrusted input, so run the
file directly with ``python -I`` (not ``-m``) and keep the dump outside the
source tree::

    ../venv/interoves_django/bin/python -I \
        games/puzzles/censorly/offline/tools/wiktionary/extract.py \
        var/wiktionary/ruwiktionary-20261101-pages-articles.xml.bz2 \
        games/puzzles/censorly/lexical/data/dictionaries

Outputs (all TSV, UTF-8, sorted, deterministic):

``wiktionary_morphs.tsv.gz``
    ``lemma homonym pos segmentation source nests``. One row per Russian
    homonym section that carries ``{{морфо-ru|…}}``. ``segmentation`` uses
    the Tikhonov ``RuMorphs-Lemmas.txt`` notation (``по:PREF/мощь:ROOT``), so
    the root bank can read it with the same parser. ``source`` is the
    template's ``и=`` flag (``т`` = Tikhonov). ``nests`` lists the
    ``{{родств:X}}`` templates the section transcludes.

``wiktionary_nests.tsv.gz``
    ``nest alternants block lemma``. Members of every ``Шаблон:родств:X``
    root nest. Nests are etymological evidence, not game families.

``wiktionary_related.tsv.gz``
    ``lemma homonym block related``. The per-page «Родственные слова» block.

``wiktionary.meta.json``
    Dump name, counts, licence.

Content is CC BY-SA 4.0 (Wiktionary contributors).
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import io
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

_RU_HEADER = re.compile(r'^=\s*\{\{-ru-\}\}\s*=\s*$', re.M)
_LANG_HEADER = re.compile(r'^=\s*\{\{-[^}]+-\}\}\s*=\s*$', re.M)
_HOMONYM_HEADER = re.compile(r'^==(?!=)\s*(.*?)\s*==\s*$', re.M)
_SECTION_HEADER = re.compile(r'^===(?!=)\s*(.*?)\s*===\s*$', re.M)
_NEST_REF = re.compile(r'\{\{\s*родств:([^|}\n]+?)\s*[|}]')
_COMMENT = re.compile(r'<!--.*?-->', re.S)
_NEST_LANGUAGE = re.compile(r'^([a-z]{2,4}):')
_LINK = re.compile(r'\[\[([^\]|]*)(?:\|[^\]]*)?\]\]')
_TAG = re.compile(r'<[^>]+>')
_CYRILLIC = re.compile('[а-яё]', re.I)
_STRESS = dict.fromkeys(map(ord, '́̀̑'), None)

_POS = (
    ('сущ', 'NOUN'), ('прил', 'ADJF'), ('гл', 'VERB'), ('adv', 'ADVB'),
    ('нареч', 'ADVB'), ('прич', 'PRTF'), ('деепр', 'GRND'), ('числ', 'NUMR'),
    ('мест', 'NPRO'), ('предикатив', 'PRED'), ('предл', 'PREP'),
    ('союз', 'CONJ'), ('част', 'PRCL'), ('межд', 'INTJ'), ('фам', 'NOUN'),
    ('имя', 'NOUN'), ('topo', 'NOUN'),
)
_NEST_SKIP_FIELDS = frozenset({
    'имя шаблона', 'и', 'корень-доп', 'и-доп', 'полн', 'коммент',
})


def clean_word(text: str) -> str:
    """Plain lowercase-preserving word: no stress, markup or link syntax."""
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _TAG.sub('', text).replace("'''", '').replace("''", '')
    text = unicodedata.normalize('NFD', text).translate(_STRESS)
    text = ' '.join(unicodedata.normalize('NFC', text).split())
    return text.strip(' .;:()[]«»"')


def split_params(body: str) -> list[str]:
    """Split a template body on top-level ``|``."""
    parts, depth, start = [], 0, 0
    index = 0
    while index < len(body):
        pair = body[index:index + 2]
        if pair in ('{{', '[['):
            depth += 1
            index += 2
            continue
        if pair in ('}}', ']]'):
            depth -= 1
            index += 2
            continue
        if body[index] == '|' and depth == 0:
            parts.append(body[start:index])
            start = index + 1
        index += 1
    parts.append(body[start:])
    return parts


def template_bodies(text: str, name: str) -> list[str]:
    """Bodies of every ``{{name|…}}`` call, nesting-aware."""
    bodies = []
    needle = re.compile(r'\{\{\s*' + re.escape(name) + r'\s*\|')
    for match in needle.finditer(text):
        depth, index = 1, match.end()
        while index < len(text) and depth:
            pair = text[index:index + 2]
            if pair == '{{':
                depth += 1
                index += 2
            elif pair == '}}':
                depth -= 1
                index += 2
            else:
                index += 1
        if depth == 0:
            bodies.append(text[match.end():index - 2])
    return bodies


def _morph(text: str) -> str:
    text = clean_word(text).lower()
    return text.replace('j', '(j)').replace('й(j)', 'й')


def segmentation(lemma: str, body: str) -> tuple[str, str]:
    """Return ``(tikhonov_segmentation, source_flag)`` for a морфо-ru body."""
    source = ''
    pieces = []
    for raw in split_params(body):
        raw = raw.strip()
        if '=' in raw:
            key, _sep, value = raw.partition('=')
            if key.strip() == 'и':
                source = value.strip()
            continue
        if not raw:
            continue
        if raw.startswith('+'):
            kind, morph = 'END', raw[1:]
        elif raw.endswith('--') or raw.startswith('--'):
            # Prefixoid «фото--» / suffixoid «--стан»: a bound root, as in
            # Tikhonov (фото:ROOT/синтез:ROOT), never a prefix or suffix.
            kind, morph = 'ROOT', raw.strip('-')
        elif raw.startswith('-') and raw.endswith('-') and len(raw) > 2:
            kind, morph = 'LINK', raw[1:-1]
        elif raw.startswith('-'):
            kind, morph = 'SUFF', raw[1:]
        elif raw.endswith('-'):
            kind, morph = 'PREF', raw[:-1]
        else:
            kind, morph = 'ROOT', raw
        morph = _morph(morph)
        if morph in ('', '∅', '0'):
            continue
        pieces.append([morph, kind])
    if pieces and pieces[-1][1] == 'SUFF' and pieces[-1][0] in ('ся', 'сь'):
        pieces[-1][1] = 'POSTFIX'
    # Re-insert orthographic hyphens of the lemma (по-русски, кое-кто).
    plain = clean_word(lemma).lower()
    out, cursor = [], 0
    for morph, kind in pieces:
        bare = morph.replace('(j)', '')
        while cursor < len(plain) and plain[cursor] == '-':
            out.append('-:HYPH')
            cursor += 1
        out.append(f'{morph}:{kind}')
        if plain.startswith(bare, cursor):
            cursor += len(bare)
    return '/'.join(out), source


def pos_of(section: str, homonym_title: str) -> str:
    for text in (homonym_title.lower(),):
        for word, tag in (('существительн', 'NOUN'), ('прилагательн', 'ADJF'),
                          ('глагол', 'VERB'), ('наречие', 'ADVB'),
                          ('причастие', 'PRTF'), ('числительн', 'NUMR'),
                          ('местоимение', 'NPRO')):
            if word in text:
                return tag
    match = re.search(r'\{\{\s*([^|}\s]+)', section)
    if not match:
        return ''
    name = match.group(1).lower()
    for prefix, tag in _POS:
        if name.startswith(prefix):
            return tag
    return ''


def russian_part(text: str) -> str:
    match = _RU_HEADER.search(text)
    if not match:
        return ''
    rest = text[match.end():]
    following = _LANG_HEADER.search(rest)
    return rest[:following.start()] if following else rest


def homonym_sections(ru: str) -> list[tuple[str, str]]:
    """Split the Russian part into ``(header, text)`` homonym sections."""
    headers = list(_HOMONYM_HEADER.finditer(ru))
    if not headers:
        return [('', ru)]
    sections = []
    if ru[:headers[0].start()].strip():
        sections.append(('', ru[:headers[0].start()]))
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(ru)
        sections.append((header.group(1), ru[header.end():end]))
    return sections


def subsection(text: str, title: str) -> str:
    headers = list(_SECTION_HEADER.finditer(text))
    for index, header in enumerate(headers):
        if header.group(1).strip().lower().startswith(title):
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            return text[header.end():end]
    return ''


def block_members(body: str):
    """Yield ``(field, word)`` from a родств-блок / родств-блок1 body."""
    for raw in split_params(_COMMENT.sub('', body)):
        if '=' not in raw:
            continue
        field, _sep, value = raw.partition('=')
        field = field.strip()
        if field in _NEST_SKIP_FIELDS or not _CYRILLIC.search(value):
            continue
        value = value.replace('&#8226;', ',').replace('•', ',')
        value = re.sub(r'<br\s*/?>', ',', value)
        for item in re.split(r'[,;]', value):
            word = clean_word(item)
            if word and _CYRILLIC.search(word) and len(word.split()) <= 3:
                yield field, word


def iter_pages(dump: Path):
    opener = bz2.open if dump.suffix == '.bz2' else open
    with opener(dump, 'rb') as stream:
        title = ns = None
        for event, elem in ET.iterparse(stream, events=('end',)):
            tag = elem.tag.rsplit('}', 1)[-1]
            if tag == 'title':
                title = elem.text or ''
            elif tag == 'ns':
                ns = elem.text
            elif tag == 'text':
                text = elem.text or ''
                yield title, ns, text
            elif tag == 'page':
                elem.clear()
                title = ns = None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('dump', type=Path)
    parser.add_argument('out_dir', type=Path)
    parser.add_argument('--limit', type=int, default=0, help='stop after N pages (debug)')
    args = parser.parse_args()

    started = time.perf_counter()
    morph_rows, nest_rows, related_rows = set(), set(), set()
    stats = Counter()
    for page_index, (title, ns, text) in enumerate(iter_pages(args.dump), 1):
        if args.limit and page_index > args.limit:
            break
        if page_index % 200_000 == 0:
            print(f'{page_index} pages, {len(morph_rows)} morph rows, '
                  f'{time.perf_counter() - started:.0f}s', file=sys.stderr, flush=True)
        if ns == '10' and title.startswith('Шаблон:родств:'):
            nest = title.split(':', 2)[2].strip()
            language = _NEST_LANGUAGE.match(nest)
            if language and language.group(1) != 'ru':
                continue  # uk:дом-дім, ja:neko — other languages' nests
            nest = _NEST_LANGUAGE.sub('', nest)
            for body in template_bodies(text, 'родств-блок1'):
                params = split_params(_COMMENT.sub('', body))
                alternants = clean_word(params[0]) if params and '=' not in params[0] else ''
                stats['nests'] += 1
                for field, word in block_members(body):
                    nest_rows.add((nest, alternants, field, word))
            continue
        if ns != '0':
            continue
        ru = russian_part(text)
        if not ru:
            continue
        stats['ru_pages'] += 1
        lemma = clean_word(title)
        homonym = 0
        page_has_morph = False
        for header, section in homonym_sections(ru):
            bodies = template_bodies(section, 'морфо-ru')
            if not bodies:
                continue
            homonym += 1
            nests = sorted({clean_word(m.group(1)) for m in _NEST_REF.finditer(section)})
            pos = pos_of(subsection(section, 'морфологические') or section, header)
            seen = set()
            for body in bodies:
                seg, source = segmentation(lemma, body)
                if not seg or ':ROOT' not in seg or seg in seen:
                    stats['morph_without_root'] += not (seg and ':ROOT' in seg)
                    continue
                seen.add(seg)
                morph_rows.add((lemma, str(homonym), pos, seg, source, ','.join(nests)))
                page_has_morph = True
            related = subsection(section, 'родственные слова')
            for body in template_bodies(related, 'родств-блок'):
                for field, word in block_members(body):
                    if word != lemma:
                        related_rows.add((lemma, str(homonym), field, word))
        stats['ru_pages_with_morph'] += page_has_morph

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, header, rows in (
        ('wiktionary_morphs.tsv.gz', 'lemma\thomonym\tpos\tsegmentation\tsource\tnests', morph_rows),
        ('wiktionary_nests.tsv.gz', 'nest\talternants\tblock\tlemma', nest_rows),
        ('wiktionary_related.tsv.gz', 'lemma\thomonym\tblock\trelated', related_rows),
    ):
        # mtime=0 keeps the archive byte-identical across reruns.
        with open(args.out_dir / name, 'wb') as raw, gzip.GzipFile(
            fileobj=raw, mode='wb', compresslevel=9, mtime=0,
        ) as packed, io.TextIOWrapper(packed, encoding='utf-8') as out:
            out.write(header + '\n')
            for row in sorted(rows):
                out.write('\t'.join(row) + '\n')
    lemmas = {row[0] for row in morph_rows}
    meta = {
        'source': args.dump.name,
        'licence': 'CC BY-SA 4.0, Wiktionary contributors (ru.wiktionary.org)',
        'extractor': 'games/puzzles/censorly/offline/tools/wiktionary/extract.py',
        'seconds': round(time.perf_counter() - started, 1),
        'morph_rows': len(morph_rows),
        'morph_lemmas': len(lemmas),
        'homonymous_lemmas': len({row[0] for row in morph_rows if row[1] != '1'}),
        'nest_rows': len(nest_rows),
        'related_rows': len(related_rows),
        **dict(stats),
    }
    (args.out_dir / 'wiktionary.meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8',
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
