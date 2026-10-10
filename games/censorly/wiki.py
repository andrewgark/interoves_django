"""Fetch Russian Wikipedia plaintext articles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

WIKI_API = 'https://ru.wikipedia.org/w/api.php'
USER_AGENT = 'InterovesCensorly/1.0 (https://interoves.com; game puzzle generator)'
MIN_BODY_CHARS = 400
# Cap for playable payloads; full Москва extract is ~100k chars / huge DOM.
MAX_BODY_CHARS = 28_000

# Trailing wiki sections that add noise for guessing (notes, links, nav).
_TAIL_SECTION_NAMES = (
    'примечания',
    'литература',
    'ссылки',
    'внешние ссылки',
    'см. также',
    'см также',
    'источники',
    'примечания и ссылки',
    'литература и ссылки',
    'галерея',
    'навигация',
    'категории',
)

_SECTION_HEADING_RE = re.compile(
    r'^(={2,})\s*(.+?)\s*\1\s*$',
    re.MULTILINE,
)

class WikiFetchError(Exception):
    """Failed to load or validate a Wikipedia article."""


# Citation footnotes [1] / [12] — not crystallographic Miller indices [100]/[010].
_NUMERIC_FOOTNOTE_RE = re.compile(r'\[(\d{1,2})\]')
# Common ruwiki editorial / citation-needed bracket notes.
_EDITORIAL_BRACKET_RE = re.compile(
    r'\[(?:'
    r'уточнить|кто\?|когда\?|где\?|какой\?|какая\?|какое\?|какие\?|сколько\?|'
    r'источник\??|нужен\s+источник|нет\s+источника'
    r')\]',
    re.IGNORECASE,
)
# «МФА:» or «английское произношение:», with or without the following […].
# The adjective is optional so a bare «произношение:» still matches, but a
# random word («имеет произношение») is left alone.
_PRONUNCIATION_LABEL_RE = re.compile(
    r'(?:,[ \t\u00a0]*)?'
    r'(?:'
    r'\bМФА\b'
    r'|'
    r'(?:\b[А-Яа-яЁё\-]*(?:ое|ее|ая|ый|ий|ой)\b[ \t\u00a0]+)?'
    r'\bпроизношение\b'
    r')'
    r'[ \t\u00a0]*:[ \t\u00a0]*'
    r'(?:\[[^\]]*\])?',
    re.IGNORECASE,
)
# IPA signs that do not show up in ordinary Russian prose or in [100]/[010].
_IPA_CHAR_RE = re.compile(
    '['
    'ˈˌːˑʲʰʷ˞ʼ'
    'əɐɪʊɛɔʌæɑɒœøɨʉɵɯɤʏ'
    'ʃʒθðŋɡɾʁɕʑɸβɣχʔɫɭɲɱʋɥɬɮɹɻɽʀʙʜʢʡʕʘǀǂǁʍ'
    ']'
)
_SQUARE_BRACKET_RE = re.compile(r'\[[^\]]*\]')
_EMPTY_PAREN_RE = re.compile(r'\([ \t\u00a0]*[,.;:!?…\-—]*[ \t\u00a0]*\)')


@dataclass(frozen=True)
class WikiArticle:
    title: str
    pageid: int
    extract: str
    truncated: bool = False
    revid: int | None = None


_TITLE_FROM_PATH = re.compile(r'^/wiki/([^?#]+)$')
_DISAMBIG_MARKERS = (
    'многозначный термин',
    'может означать',
    'может относиться',
    'список значений',
)


def title_from_user_input(raw: str) -> str:
    """Accept a bare title or ru.wikipedia.org URL."""
    text = (raw or '').strip()
    if not text:
        raise WikiFetchError('Пустой заголовок')
    if 'wikipedia.org' in text or text.startswith('http://') or text.startswith('https://'):
        parsed = urlparse(text)
        m = _TITLE_FROM_PATH.match(parsed.path or '')
        if m:
            return unquote(m.group(1).replace('_', ' '))
        qs = parse_qs(parsed.query or '')
        if qs.get('title'):
            return unquote(qs['title'][0].replace('_', ' '))
        raise WikiFetchError('Не удалось разобрать URL Википедии')
    return text


def _looks_like_disambiguation(extract: str) -> bool:
    head = (extract or '')[:400].lower().replace('ё', 'е')
    return any(marker in head for marker in _DISAMBIG_MARKERS)


def _normalize_section_name(name: str) -> str:
    return (name or '').strip().lower().replace('ё', 'е').rstrip('.')


def _strip_tail_sections(extract: str) -> str:
    """Drop Примечания / Ссылки / … and everything after the first such heading."""
    if not extract:
        return extract
    earliest = None
    for match in _SECTION_HEADING_RE.finditer(extract):
        name = _normalize_section_name(match.group(2))
        # Exact name, or "Ссылки …" / "Примечания и …" — not "Литературный обзор".
        is_tail = name in _TAIL_SECTION_NAMES or any(
            name == base or name.startswith(base + ' ') or name.startswith(base + ' и ')
            for base in _TAIL_SECTION_NAMES
        )
        if is_tail:
            earliest = match.start() if earliest is None else min(earliest, match.start())
    if earliest is None or earliest < MIN_BODY_CHARS:
        return extract
    return extract[:earliest].rstrip()


def _strip_orphan_heading(extract: str) -> str:
    """Remove a trailing == Section == with no body after it."""
    text = (extract or '').rstrip()
    match = list(_SECTION_HEADING_RE.finditer(text))
    if not match:
        return text
    last = match[-1]
    after = text[last.end():].strip()
    if after:
        return text
    # Keep if the whole article is somehow just a heading (shouldn't happen).
    if last.start() < MIN_BODY_CHARS // 2:
        return text
    return text[: last.start()].rstrip()


_TEX_MATH_START_RE = re.compile(r'\{\\(?:displaystyle|textstyle)\b')
_TEX_TEXT_ARROWS = (
    ('<=>', '⇌'), ('<->', '⇌'), ('->', '→'), ('<-', '←'),
    ('=>', '⇒'), ('<=', '⇐'),
)
_TEX_SYMBOLS = {
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ',
    'epsilon': 'ε', 'varepsilon': 'ϵ', 'zeta': 'ζ', 'eta': 'η',
    'theta': 'θ', 'vartheta': 'ϑ', 'iota': 'ι', 'kappa': 'κ',
    'lambda': 'λ', 'mu': 'μ', 'nu': 'ν', 'xi': 'ξ', 'pi': 'π',
    'varpi': 'ϖ', 'rho': 'ρ', 'varrho': 'ϱ', 'sigma': 'σ',
    'varsigma': 'ς', 'tau': 'τ', 'upsilon': 'υ', 'phi': 'φ',
    'varphi': 'ϕ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
    'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ', 'Lambda': 'Λ',
    'Xi': 'Ξ', 'Pi': 'Π', 'Sigma': 'Σ', 'Upsilon': 'Υ',
    'Phi': 'Φ', 'Psi': 'Ψ', 'Omega': 'Ω',
    'hbar': 'ℏ', 'ell': 'ℓ', 'partial': '∂', 'nabla': '∇',
    'infty': '∞', 'times': '×', 'cdot': '·', 'pm': '±', 'mp': '∓',
    'le': '≤', 'leq': '≤', 'ge': '≥', 'geq': '≥', 'neq': '≠',
    'ne': '≠', 'approx': '≈', 'equiv': '≡', 'notin': '∉',
    'in': '∈', 'to': '→', 'rightarrow': '→', 'longrightarrow': '⟶',
    'leftarrow': '←', 'longleftarrow': '⟵', 'leftrightarrow': '↔',
    'longleftrightarrow': '⟷', 'Rightarrow': '⇒', 'Longrightarrow': '⟹',
    'Leftarrow': '⇐', 'Longleftarrow': '⟸', 'Leftrightarrow': '⇔',
    'Longleftrightarrow': '⟺', 'mapsto': '↦', 'langle': '⟨', 'rangle': '⟩',
    'cup': '∪', 'cap': '∩', 'land': '∧',
    'lor': '∨', 'degree': '°', 'circ': '∘', 'emptyset': '∅',
    'triangle': '△', 'perp': '⊥', 'parallel': '∥', 'otimes': '⊗',
    'sim': '∼', 'subseteq': '⊆', 'mid': '∣', 'angle': '∠', 'prime': '′',
    'll': '≪',
    'ldots': '…', 'cdots': '…', 'dots': '…', 'over': ' / ',
    'int': '∫', 'iint': '∬', 'iiint': '∭', 'oint': '∮',
    'sum': '∑', 'prod': '∏', 'sqrt': '√',
}
_TEX_FORMAT_COMMANDS = frozenset({
    'displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle',
    'limits', 'nolimits', 'left', 'right', 'middle', 'big', 'Big', 'bigg', 'Bigg',
    'bigl', 'bigr', 'Bigl', 'Bigr', 'biggl', 'biggr', 'Biggl', 'Biggr', 'quad', 'qquad',
    '!', ',', ';', ':', ' ',
})
_TEX_GROUP_COMMANDS = frozenset({
    'operatorname', 'operatorname*', 'mathrm', 'mathbf', 'mathit',
    'mathsf', 'mathtt', 'mathcal', 'mathbb', 'mathfrak', 'text', 'mbox', 'rm',
})
_TEX_FUNCTION_COMMANDS = frozenset({
    'arg', 'cos', 'cosh', 'cot', 'coth', 'csc', 'deg', 'det', 'dim',
    'exp', 'gcd', 'hom', 'inf', 'ker', 'lg', 'lim', 'liminf', 'limsup',
    'ln', 'log', 'max', 'min', 'Pr', 'sec', 'sin', 'sinh', 'sup', 'tan',
    'tanh',
})
_SUPERSCRIPTS = str.maketrans({
    **dict(zip('0123456789+-=()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾')),
    **dict(zip('abcdefghijklmnoprstuvwxyz', 'ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ')),
})
_SUBSCRIPTS = str.maketrans({
    **dict(zip('0123456789+-=()', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎')),
    **dict(zip('aehijklmnoprstuvx', 'ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ')),
})
_SCRIPT_CHARS = '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ⁰¹²³⁴⁵⁶⁷⁸⁹'
_SCRIPT_ATOM_RE = re.compile(r'[\wΑ-Ωα-ωϵϑϖϱςℏℓ]+[₀-₉₊₋₌₍₎ₐ-ₓᵃ-ᶻ⁰-⁹⁺⁻⁼⁽⁾]*$', re.UNICODE)


def _tex_group(text: str, start: int) -> tuple[str, int] | None:
    """Read a balanced {...} group, returning its contents and end offset."""
    if start >= len(text) or text[start] != '{':
        return None
    depth = 0
    for index in range(start, len(text)):
        slashes = 0
        cursor = index - 1
        while cursor >= start and text[cursor] == '\\':
            slashes += 1
            cursor -= 1
        escaped = slashes % 2 == 1
        if text[index] == '{' and not escaped:
            depth += 1
        elif text[index] == '}' and not escaped:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return None


class _TexTextParser:
    """Render the common subset found in Wikipedia TextExtracts as readable text."""

    def __init__(self, text: str):
        self.text = text

    @staticmethod
    def _needs_implicit_product(previous: str) -> bool:
        if not previous or not (previous[-1].isalnum() or previous[-1] == ')'):
            return False
        final_word = re.search(r'([A-Za-z]+)$', previous)
        return not final_word or final_word.group(1) not in _TEX_FUNCTION_COMMANDS

    def _argument(self, pos: int) -> tuple[str, int] | None:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        if pos >= len(self.text):
            return None
        if self.text[pos] == '{':
            group = _tex_group(self.text, pos)
            if group is None:
                return None
            value, end = group
            return _TexTextParser(value).parse(), end
        if self.text[pos] == '\\':
            value, end = self._command(pos)
            return value, end
        return self.text[pos], pos + 1

    def _command(self, pos: int) -> tuple[str, int]:
        j = pos + 1
        if j < len(self.text) and self.text[j].isalpha():
            while j < len(self.text) and self.text[j].isalpha():
                j += 1
            name = self.text[pos + 1:j]
        elif j < len(self.text):
            name = self.text[j]
            j += 1
        else:
            return '\\', j

        if name == 'operatorname' and j < len(self.text) and self.text[j] == '*':
            name = 'operatorname*'
            j += 1

        if name in ('frac', 'dfrac', 'tfrac'):
            numerator = self._argument(j)
            denominator = self._argument(numerator[1]) if numerator else None
            if numerator and denominator:
                return f'({numerator[0]})/({denominator[0]})', denominator[1]
            return 'frac', j
        if name == 'sqrt':
            if j < len(self.text) and self.text[j] == '[':
                close = self.text.find(']', j + 1)
                if close >= 0:
                    index = _TexTextParser(self.text[j + 1:close]).parse()
                    radicand = self._argument(close + 1)
                    if radicand:
                        superscript = index.translate(_SUPERSCRIPTS)
                        prefix = superscript if superscript != index else f'({index})'
                        return f'{prefix}√({radicand[0]})', radicand[1]
            radicand = self._argument(j)
            if radicand:
                return f'√({radicand[0]})', radicand[1]
            return '√', j
        if name == 'ce':
            formula = self._argument(j)
            if formula:
                # mhchem writes stoichiometric counts as ordinary digits.
                rendered = re.sub(
                    r'(?<=[A-Za-z)])([0-9]+)',
                    lambda match: match.group(1).translate(_SUBSCRIPTS),
                    formula[0],
                )
                return rendered, formula[1]
        if name in _TEX_FORMAT_COMMANDS:
            return (' ' if name in ('quad', 'qquad', ',', ';', ':', ' ') else ''), j
        if name in _TEX_GROUP_COMMANDS or name in ('overline', 'bar', 'vec', 'hat', 'dot', 'ddot', 'acute', 'tilde'):
            arg = self._argument(j)
            if not arg:
                return name, j
            if name in _TEX_GROUP_COMMANDS:
                return arg
            if name in ('dot', 'ddot') and _SCRIPT_ATOM_RE.fullmatch(arg[0]):
                return f"{arg[0]}{'˙' if name == 'dot' else '¨'}", arg[1]
            if name in ('overline', 'bar') and _SCRIPT_ATOM_RE.fullmatch(arg[0]):
                return f'{arg[0]}¯', arg[1]
            return f'{name}({arg[0]})', arg[1]
        if name == 'begin' or name == 'end':
            env = self._argument(j)
            return ('; ' if name == 'end' and env and env[0] in ('cases', 'aligned', 'array') else ''), (env[1] if env else j)
        if name == '\\':
            return '; ', j
        if name in ('{', '}'):
            return name, j
        if name == '.':
            return '', j
        if name == '&':
            return ' ', j
        if name in _TEX_SYMBOLS:
            return _TEX_SYMBOLS[name], j
        if name in _TEX_FUNCTION_COMMANDS:
            # TeX function commands are words, so keep them token-separated
            # from adjacent variables even when the source has no whitespace.
            return f' {name} ', j
        # Unknown commands remain legible instead of disappearing or leaking a slash.
        return name, j

    def parse(self) -> str:
        out: list[str] = []
        i = 0
        while i < len(self.text):
            arrow = next(
                ((source, replacement) for source, replacement in _TEX_TEXT_ARROWS
                 if self.text.startswith(source, i)),
                None,
            )
            if arrow:
                source, replacement = arrow
                out.append(f' {replacement} ')
                i += len(source)
                continue
            ch = self.text[i]
            if ch == '\\':
                previous = ''.join(out).rstrip()
                value, i = self._command(i)
                if re.fullmatch(r'\(.+\)/\(.+\)', value) and self._needs_implicit_product(previous):
                    out.append(' · ')
                out.append(value)
                continue
            if ch == '{':
                group = _tex_group(self.text, i)
                if group is None:
                    # Incomplete TextExtracts occasionally end mid-formula.
                    # Parse the available fragment without letting it leak TeX.
                    out.append(_TexTextParser(self.text[i + 1:]).parse())
                    break
                value, i = group
                rendered_group = _TexTextParser(value).parse()
                previous = ''.join(out).rstrip()
                if re.fullmatch(r'\(.+\)/\(.+\)', rendered_group) and self._needs_implicit_product(previous):
                    out.append(' · ')
                out.append(rendered_group)
                continue
            if ch == '&':
                out.append(' ')
                i += 1
                continue
            if ch == '-':
                out.append('−')
                i += 1
                continue
            if ch in ('^', '_'):
                arg = self._argument(i + 1)
                if arg:
                    value, i = arg
                    trans = _SUPERSCRIPTS if ch == '^' else _SUBSCRIPTS
                    translated = value.translate(trans)
                    if translated != value:
                        out.append(translated)
                    elif ch == '_':
                        out.append(f'₍{value}₎')
                    else:
                        out.append(f'^({value})')
                    continue
            out.append(ch)
            i += 1
        rendered = re.sub(r'[ \t\r\n]+', ' ', ''.join(out)).strip()
        rendered = re.sub(r'\s*([=+−×·≤≥≠≈])\s*', r' \1 ', rendered)
        rendered = re.sub(r'= +− +(?=[(\d])', '= −', rendered)
        rendered = re.sub(r'([⟨])\s+', r'\1', rendered)
        rendered = re.sub(r'\s+([⟩])', r'\1', rendered)
        scripts = re.escape(_SCRIPT_CHARS)
        rendered = re.sub(rf'([∫∬∭∮])\s+([{scripts}]+)', r'\1\2', rendered)
        rendered = re.sub(rf'([∫∬∭∮][{scripts}]+)(?=[A-Za-zА-Яа-яα-ωΑ-Ω])', r'\1 ', rendered)
        rendered = re.sub(rf'([A-Za-zΑ-Ωα-ωℏ])\s+([{scripts}]+)', r'\1\2', rendered)
        return re.sub(r' {2,}', ' ', rendered).strip()


def _replace_tex_groups(text: str) -> str:
    """Convert balanced TeX source groups to readable formula text."""
    if not text or '\\' not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '{' and i + 1 < n and text[i + 1] == '\\':
            group = _tex_group(text, i)
            if group is None:
                # Best-effort conversion for a truncated final formula.
                out.append(_TexTextParser(text[i + 1:]).parse())
                break
            formula, i = group
            out.append(_TexTextParser(formula).parse())
            continue
        out.append(text[i])
        i += 1
    return ''.join(out)


def _strip_indented_math_lines(text: str) -> str:
    """Drop only indented MathML fallback blocks immediately before TeX source."""
    if not text:
        return text
    kept: list[str] = []
    pending: list[str] = []
    for line in text.splitlines(keepends=True):
        indented = line.startswith((' ', '\t'))
        has_tex = bool(_TEX_MATH_START_RE.search(line))
        if has_tex:
            # TextExtracts emits a vertical MathML text fallback before its
            # canonical TeX source. The TeX form is enough to render it once.
            pending.clear()
            kept.append(line)
        elif indented and not re.search(r'[А-Яа-яЁё]{2,}', line):
            pending.append(line)
        elif not line.strip():
            pending.append(line)
        else:
            kept.extend(pending)
            pending.clear()
            kept.append(line)
    kept.extend(pending)
    return ''.join(kept)


def _collapse_extract_whitespace(text: str) -> str:
    """Normalize blanks after math/image cleanup so prose reads as paragraphs."""
    if not text:
        return text
    text = text.replace('\u2061', '')  # function-application (MathML)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Punctuation that followed a removed display formula.
    text = re.sub(r'\n{2,}[ \t]*([,.;:!?…])', r'\1', text)
    # Mid-sentence leftovers: "скорость\n\nсоответствует" / "скорость\n соответствует".
    text = re.sub(r'([^\n])\n{2,}[ \t]*([^\W\d_])', r'\1 \2', text)
    text = re.sub(r'([^\n])\n[ \t]*([^\W\d_])', r'\1 \2', text)
    # A run of formula-terminating semicolons, not a single prose semicolon.
    text = re.sub(r';{2,}', '', text)
    text = re.sub(r';\s*([.,])', r'\1', text)
    text = re.sub(r'([.!?…]);', r'\1', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\(\s+', '(', text)
    text = re.sub(r'\s+\)', ')', text)
    text = _EMPTY_PAREN_RE.sub('', text)
    return text.strip()


def _strip_ipa_brackets(text: str) -> str:
    """Drop […] groups that contain phonetic signs. Keep [100]/[010]/[001]."""
    def repl(match: re.Match[str]) -> str:
        group = match.group(0)
        if re.search(r'\\[A-Za-z]+|[=+−×*/^_∫∑∂∇≤≥]', group):
            return group  # Square-bracketed formula or vector notation.
        if _IPA_CHAR_RE.search(group):
            return ''
        return group

    return _SQUARE_BRACKET_RE.sub(repl, text)


def _tidy_pronunciation_gaps(text: str) -> str:
    """Close holes left where a pronunciation dump used to sit."""
    text = re.sub(r'\([ \t\u00a0]+', '(', text)
    text = re.sub(r'[ \t\u00a0]+\)', ')', text)
    text = _EMPTY_PAREN_RE.sub('', text)
    # «Shakespeare, ; 26» after the pronunciation between the separators is gone.
    text = re.sub(r'([,;])[ \t\u00a0]*([,;])', r'\2', text)
    text = re.sub(r'[ \t\u00a0]+([,.;:!?…])', r'\1', text)
    text = re.sub(r'[ \t\u00a0]{2,}', ' ', text)
    text = _EMPTY_PAREN_RE.sub('', text)
    return text


def _strip_pronunciations(text: str) -> str:
    """Drop ruwiki lead transcriptions that explaintext flattens out of .IPA."""
    if not text:
        return text
    text = _PRONUNCIATION_LABEL_RE.sub('', text)
    text = _strip_ipa_brackets(text)
    return _tidy_pronunciation_gaps(text)


def _strip_bracket_notes(text: str) -> str:
    """Drop citation footnotes and editorial [уточнить]-style notes; keep [100]/Miller."""
    if not text:
        return text
    text = _strip_pronunciations(text)
    if '[' not in text:
        return text
    text = _NUMERIC_FOOTNOTE_RE.sub('', text)
    text = _EDITORIAL_BRACKET_RE.sub('', text)
    # "слово  ." / "слово ," after note removal
    text = re.sub(r' +([,.;:!?…])', r'\1', text)
    text = re.sub(r' {2,}', ' ', text)
    return text


def clean_wiki_extract(extract: str) -> str:
    """Convert TextExtracts math dumps to readable, tokenizable raw text."""
    text = extract or ''
    text = _strip_indented_math_lines(text)
    text = _replace_tex_groups(text)
    text = _strip_bracket_notes(text)
    return _collapse_extract_whitespace(text)


def _headings_to_marked(extract: str) -> str:
    """Wrap == Heading == as marked spans for larger play UI (tokenize in_heading)."""
    from games.censorly.tokenize import HEADING_END, HEADING_LEVEL_SEP, HEADING_START

    def repl(match: re.Match[str]) -> str:
        name = (match.group(2) or '').strip()
        if not name:
            return ''
        # == is a section (2), === a subsection (3), and so on, same as HTML h2–h6.
        level = min(6, max(2, len(match.group(1) or '==')))
        return f'{HEADING_START}{level}{HEADING_LEVEL_SEP}{name}{HEADING_END}'

    return _SECTION_HEADING_RE.sub(repl, extract or '')


def _trim_extract(extract: str) -> tuple[str, bool]:
    """Return (text, truncated). Prefer cutting before a section heading."""
    text = clean_wiki_extract(extract or '')
    text = _strip_tail_sections(text)
    text = _strip_orphan_heading(text)
    if len(text) <= MAX_BODY_CHARS:
        return _headings_to_marked(text), False

    cut = text[:MAX_BODY_CHARS]
    # Prefer ending just before the last full section heading in the window.
    best = -1
    for match in _SECTION_HEADING_RE.finditer(cut):
        if match.start() >= MIN_BODY_CHARS:
            best = match.start()
    if best >= MIN_BODY_CHARS:
        trimmed = cut[:best].rstrip()
    else:
        trimmed = cut
        for sep in ('\n\n', '\n', '. '):
            idx = trimmed.rfind(sep)
            if idx >= MIN_BODY_CHARS:
                trimmed = trimmed[: idx + len(sep)].rstrip()
                break
        else:
            trimmed = trimmed.rstrip()
    trimmed = _strip_orphan_heading(trimmed)
    return _headings_to_marked(trimmed), True


def fetch_article(title: str, *, session: Optional[requests.Session] = None) -> WikiArticle:
    """Load plaintext extract for a Russian Wikipedia title."""
    title = title_from_user_input(title)
    sess = session or requests.Session()
    params = {
        'action': 'query',
        'format': 'json',
        'prop': 'extracts|info|pageprops',
        'ppprop': 'disambiguation',
        'explaintext': 1,
        # wiki headings (== Name ==) so we can strip tails / trim on sections.
        'exsectionformat': 'wiki',
        'redirects': 1,
        'titles': title,
        'inprop': 'displaytitle',
    }
    try:
        resp = sess.get(
            WIKI_API,
            params=params,
            headers={'User-Agent': USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise WikiFetchError(f'Не удалось загрузить статью: {exc}') from exc
    except ValueError as exc:
        raise WikiFetchError('Некорректный ответ Wikipedia API') from exc

    pages = (data.get('query') or {}).get('pages') or {}
    if not pages:
        raise WikiFetchError('Статья не найдена')
    page = next(iter(pages.values()))
    if page.get('missing') is not None or int(page.get('pageid') or 0) < 0:
        raise WikiFetchError('Статья не найдена')
    extract = (page.get('extract') or '').strip()
    resolved_title = (page.get('title') or title).strip()
    pageprops = page.get('pageprops') or {}
    if 'disambiguation' in pageprops:
        raise WikiFetchError('Это страница неоднозначности')
    if _looks_like_disambiguation(extract):
        raise WikiFetchError('Похоже на страницу неоднозначности')
    if len(extract) < MIN_BODY_CHARS:
        raise WikiFetchError('Статья слишком короткая')
    extract, truncated = _trim_extract(extract)
    if len(extract) < MIN_BODY_CHARS:
        raise WikiFetchError('Статья слишком короткая после очистки')
    revid_raw = page.get('lastrevid')
    try:
        revid = int(revid_raw) if revid_raw else None
    except (TypeError, ValueError):
        revid = None
    return WikiArticle(
        title=resolved_title,
        pageid=int(page['pageid']),
        extract=extract,
        truncated=truncated,
        revid=revid,
    )
