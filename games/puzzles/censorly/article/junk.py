"""Wiki service marks and parser leftovers that must not reach a player.

Each pattern is something the article parser once let through: a template's
help mark, an editorial note, a TeX command that the formula cleaner did not
know. ``find_junk`` lists the ones present in a cleaned article text; the
offline ``review.junk_audit`` tool runs it over the corpus and the played
articles, so a parser change that brings one back shows up as a count.

Code listings are skipped: their text is shown as written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from games.puzzles.censorly.text.tokenize import CODE_END, CODE_START, FORMULA_END, FORMULA_START


@dataclass(frozen=True)
class JunkPattern:
    name: str
    regex: re.Pattern[str]
    note: str
    lead_only: bool = False
    formula_only: bool = False


def _p(name: str, pattern: str, note: str, **where: bool) -> JunkPattern:
    return JunkPattern(name, re.compile(pattern), note, **where)


JUNK_PATTERNS: tuple[JunkPattern, ...] = (
    _p('script_help_mark', r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]\?',
       '{{lang-ko}} «?» font help after hangul or hanja'),
    _p('wikidata_more_sources', r'(?<=[\w)])\[…\]',
       'Wikidata «[…]» more-sources link after a date or place'),
    _p('editorial_note',
       # The question words need «?»: «[что]» inside a quotation is the
       # quoting author's insertion.
       r'\[(?:(?:кем|кто|что|когда|где|чей|какой|какая|какое|какие|сколько)\?'
       r'|прояснить|уточнить|значимость факта\??|обновить данные|нет в источнике'
       r'|источник не указан[^\]]*|нет АИ[^\]]*)\]',
       'ts-fix-template note: [кем?], [прояснить]'),
    _p('dead_link_note', r'\(недоступная ссылка', '{{Недоступная ссылка}}'),
    _p('checked_on', r'Проверено[ \u00a0]\d{1,2}[ \u00a0][а-яё]+[ \u00a0]\d{4}',
       'tail of {{Недоступная ссылка}}'),
    _p('zero_width_space', r'[\u200b\ufeff]', 'U+200B / U+FEFF'),
    _p('negation_overlay', r'\u0338', '«=» + U+0338 not folded into «≠»'),
    _p('coordinate_duplicate', r'[звюс]\.[ \u00a0][дш]\.\s*/\s*\d',
       '{{coord}} decimal pair after degrees-minutes'),
    _p('empty_script', r'₍₎', 'empty subscript from a phantom-only script'),
    _p('degree_ring', r'\^\([∘°]\)', '^\\circ left as a superscript ring'),
    _p('tex_command_word',
       r'(?:overline|underline|xrightarrow|xleftarrow|colon|forall|subset|subseteq'
       r'|exists|bigcup|bigcap|mathbf|mathrm|operatorname|displaystyle|frac|sqrt|left|right)(?![A-Za-z])',
       'a TeX command name the formula cleaner did not render', formula_only=True),
    _p('lead_separator', r'\([ \u00a0]*[,;]|[,;][ \u00a0]*\)',
       'separator left in the lead parentheses after the transcription is cut',
       lead_only=True),
)

_CODE_RE = re.compile(re.escape(CODE_START) + r'.*?(?:' + re.escape(CODE_END) + r'|$)', re.DOTALL)
_FORMULA_RE = re.compile(re.escape(FORMULA_START) + r'.*?(?:' + re.escape(FORMULA_END) + r'|$)', re.DOTALL)


@dataclass(frozen=True)
class JunkHit:
    pattern: str
    start: int
    context: str


def find_junk(text: str, *, context: int = 40) -> list[JunkHit]:
    """Junk in a cleaned article text, in text order."""
    text = _CODE_RE.sub(lambda match: ' ' * len(match.group(0)), text or '')
    lead_end = text.find('\n\n')
    lead_end = len(text) if lead_end < 0 else lead_end
    formulas = [match.span() for match in _FORMULA_RE.finditer(text)]
    hits = []
    for pattern in JUNK_PATTERNS:
        for match in pattern.regex.finditer(text):
            if pattern.lead_only and match.start() >= lead_end:
                continue
            if pattern.formula_only and not any(start <= match.start() < end for start, end in formulas):
                continue
            hits.append(JunkHit(
                pattern.name, match.start(),
                text[max(0, match.start() - context):match.end() + context].replace('\n', ' '),
            ))
    return sorted(hits, key=lambda hit: hit.start)
