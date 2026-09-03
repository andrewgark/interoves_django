"""Structured payloads for daily-game share cards (ladder / salad / alphabetty).

The JavaScript SVG renderer is the production drawing path. This module only
builds locale-aware structured data — it never parses emoji share strings and
never includes puzzle answers.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Optional

from games.share_result import format_elapsed_compact

RENDERER_VERSION = '3'
CARD_WIDTH = 1080
CARD_HEIGHT = 1920

KIND_LADDER = 'ladder'
KIND_SALAD = 'salad'
KIND_ALPHABETTY = 'alphabetty'

HEADLINE_COMPACT = 'compact'
HEADLINE_SOLVED_IN = 'solved_in'

BRAND_HOST = 'interoves.com'

_MONTHS_RU = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
_MONTHS_EN = (
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
)

_GAME_TITLES = {
    KIND_LADDER: {'ru': 'Лесенка', 'en': 'Ladder'},
    KIND_SALAD: {'ru': 'Салатик', 'en': 'Salad'},
    KIND_ALPHABETTY: {'ru': 'Алфавитка', 'en': 'Alphabetty'},
}

_SOLVED_VERB = {
    KIND_LADDER: {'ru': 'решена за', 'en': 'solved in'},
    KIND_SALAD: {'ru': 'решён за', 'en': 'solved in'},
    KIND_ALPHABETTY: {'ru': 'решена за', 'en': 'solved in'},
}


def normalize_locale(locale: str | None) -> str:
    raw = (locale or 'ru').strip().lower().replace('_', '-')
    if raw.startswith('en'):
        return 'en'
    return 'ru'


def dumps_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def game_title(kind: str, locale: str) -> str:
    loc = normalize_locale(locale)
    return _GAME_TITLES.get(kind, {}).get(loc) or _GAME_TITLES.get(kind, {}).get('ru') or kind


def format_share_date(value: date | datetime | str | None, locale: str) -> str:
    loc = normalize_locale(locale)
    parsed = _as_date(value)
    if parsed is None:
        return ''
    if loc == 'en':
        return '{} {}, {}'.format(_MONTHS_EN[parsed.month - 1], parsed.day, parsed.year)
    return '{} {} {}'.format(parsed.day, _MONTHS_RU[parsed.month - 1], parsed.year)


def edition_title(kind: str, number: int | str | None, locale: str) -> str:
    title = game_title(kind, locale)
    display = str(number or '').strip()
    if not display:
        return title
    return '{} #{}'.format(title, display)


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _letter_count(word: str) -> int:
    return sum(1 for ch in str(word or '') if ch.isalpha())


def _share_grid_letters(grid) -> list[str]:
    if not grid:
        return []
    try:
        from games.word_salad import parse_grid

        return parse_grid(grid)
    except (TypeError, ValueError):
        letters = []
        for cell in grid:
            text = str(cell or '').strip().upper().replace('Ё', 'Е')
            if text:
                letters.append(text[0])
        return letters[:16] if len(letters) >= 16 else []


def _ru_plural_word(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and n100 not in (12, 13, 14):
        return few
    return many


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    return '{} {}'.format(abs(int(n)), _ru_plural_word(n, one, few, many))


def _hints_label(count: int, locale: str) -> str:
    loc = normalize_locale(locale)
    n = max(0, int(count))
    if n <= 0:
        return 'без подсказок' if loc == 'ru' else 'no hints'
    if loc == 'en':
        return '1 hint' if n == 1 else '{} hints'.format(n)
    return _ru_plural(n, 'подсказка', 'подсказки', 'подсказок')


def _attempts_word(count: int, locale: str) -> str:
    loc = normalize_locale(locale)
    n = max(0, int(count))
    if loc == 'en':
        return 'try' if n == 1 else 'tries'
    return _ru_plural_word(n, 'попытка', 'попытки', 'попыток')


def _attempts_label(count: int, locale: str) -> str:
    n = max(0, int(count))
    return '{} {}'.format(n, _attempts_word(n, locale))


def build_headline(
    *,
    kind: str,
    locale: str,
    elapsed_seconds: int | None,
    number: int | str | None = None,
    style: str = HEADLINE_SOLVED_IN,
) -> str:
    loc = normalize_locale(locale)
    title = edition_title(kind, number, loc)
    if elapsed_seconds is None:
        return title
    clock = format_elapsed_compact(elapsed_seconds)
    if style == HEADLINE_COMPACT:
        return '{} · {}'.format(title, clock)
    verb = _SOLVED_VERB[kind][loc]
    return '{} {} {}'.format(title, verb, clock)


def build_stats_line(parts: Iterable[str | None]) -> str:
    return ' · '.join(part for part in parts if part)


def _base_payload(
    *,
    kind: str,
    number: int | str | None,
    locale: str,
    date_value: date | datetime | str | None,
    elapsed_seconds: int | None,
    headline_style: str,
    brand_host: str,
    extra_stats: Iterable[str | None],
    seed: int | None = None,
) -> dict[str, Any]:
    loc = normalize_locale(locale)
    display_number = str(number or '').strip()
    try:
        seed_value = int(seed if seed is not None else display_number or 0)
    except (TypeError, ValueError):
        seed_value = 0
    headline = build_headline(
        kind=kind,
        locale=loc,
        elapsed_seconds=elapsed_seconds,
        number=display_number,
        style=headline_style,
    )
    clock = format_elapsed_compact(elapsed_seconds) if elapsed_seconds is not None else ''
    return {
        'kind': kind,
        'game_kind': kind,
        'renderer_version': RENDERER_VERSION,
        'width': CARD_WIDTH,
        'height': CARD_HEIGHT,
        'locale': loc,
        'number': display_number,
        'seed': seed_value,
        'title': edition_title(kind, display_number, loc),
        'date_label': format_share_date(date_value, loc),
        'headline': headline,
        'headline_style': headline_style,
        'elapsed_seconds': None if elapsed_seconds is None else max(0, int(elapsed_seconds)),
        'elapsed_compact': clock,
        'stats_line': build_stats_line(extra_stats),
        'brand': brand_host or BRAND_HOST,
        'filename': _filename(kind, display_number, loc),
    }


def _filename(kind: str, number: str, locale: str) -> str:
    slug = {
        KIND_LADDER: 'ladder',
        KIND_SALAD: 'salad',
        KIND_ALPHABETTY: 'alphabetty',
    }.get(kind, kind)
    parts = ['interoves', slug]
    if number:
        parts.append(str(number))
    if locale and locale != 'ru':
        parts.append(locale)
    return '{}'.format('-'.join(parts)) + '.png'


def _middle_state(tier: int, solved: bool) -> str:
    if not solved:
        return 'empty'
    if tier >= 2:
        return 'red'
    if tier == 1:
        return 'yellow'
    return 'green'


def build_ladder_share_payload(
    *,
    parsed: dict[str, Any],
    state: dict[str, Any] | None = None,
    hint_attempts=None,
    number: int | str | None = None,
    date_value: date | datetime | str | None = None,
    elapsed_seconds: int | None = None,
    locale: str = 'ru',
    headline_style: str = HEADLINE_SOLVED_IN,
    brand_host: str = BRAND_HOST,
) -> dict[str, Any]:
    from games.raddle import resolve_assist_tiers

    words = list(parsed.get('words') or [])
    n = int(parsed.get('n_words') or len(words) or 0)
    solved = set()
    for raw in (state or {}).get('solved_indices') or []:
        try:
            solved.add(int(raw))
        except (TypeError, ValueError):
            continue
    tiers = resolve_assist_tiers(state or {}, hint_attempts)
    steps = []
    hint_count = 0
    for index in range(n):
        word = words[index] if index < len(words) else ''
        length = max(1, _letter_count(word))
        if index == 0:
            role, status = 'start', 'given'
        elif index == n - 1:
            role, status = 'end', 'given'
        else:
            role = 'middle'
            status = _middle_state(int(tiers.get(index, 0) or 0), index in solved)
            if status in ('yellow', 'red'):
                hint_count += 1
        step = {'role': role, 'length': length, 'state': status}
        if role in ('start', 'end'):
            label = str(word or '').strip()
            if label:
                step['label'] = label
        steps.append(step)
    payload = _base_payload(
        kind=KIND_LADDER,
        number=number,
        locale=locale,
        date_value=date_value,
        elapsed_seconds=elapsed_seconds,
        headline_style=headline_style,
        brand_host=brand_host,
        extra_stats=(_hints_label(hint_count, locale),),
        seed=number,
    )
    payload['steps'] = steps
    payload['hint_count'] = hint_count
    return payload


def build_salad_share_payload(
    *,
    words: list,
    state: dict[str, Any] | None = None,
    number: int | str | None = None,
    date_value: date | datetime | str | None = None,
    elapsed_seconds: int | None = None,
    locale: str = 'ru',
    headline_style: str = HEADLINE_SOLVED_IN,
    brand_host: str = BRAND_HOST,
    theme: str | None = None,
    word_count: int | None = None,
    grid=None,
) -> dict[str, Any]:
    from games.word_salad import load_state, words_in_display_order

    loaded = load_state(state)
    hint_counts = loaded.get('hint_counts') or {}
    ordered = list(words_in_display_order(words)) if words else []
    word_results = []
    hint_total = 0
    for index, _word in ordered:
        raw = hint_counts.get(index, hint_counts.get(str(index), 0))
        try:
            count = max(0, int(raw or 0))
        except (TypeError, ValueError):
            count = 0
        word_results.append({'hint_count': count})
        hint_total += count
    count = word_count if word_count is not None else len(word_results)
    payload = _base_payload(
        kind=KIND_SALAD,
        number=number,
        locale=locale,
        date_value=date_value,
        elapsed_seconds=elapsed_seconds,
        headline_style=headline_style,
        brand_host=brand_host,
        extra_stats=(_hints_label(hint_total, locale),),
        seed=number,
    )
    payload['word_results'] = word_results
    payload['word_count'] = count
    payload['hint_total'] = hint_total
    letters = _share_grid_letters(grid)
    if letters:
        payload['grid'] = letters
    theme_text = (theme or '').strip()
    if theme_text:
        payload['theme'] = theme_text
    return payload


def build_alphabetty_share_payload(
    *,
    number: int | str | None = None,
    date_value: date | datetime | str | None = None,
    elapsed_seconds: int | None = None,
    attempts: int = 0,
    hints: int = 0,
    locale: str = 'ru',
    headline_style: str = HEADLINE_SOLVED_IN,
    brand_host: str = BRAND_HOST,
    variant: int | None = None,
) -> dict[str, Any]:
    loc = normalize_locale(locale)
    extra = [_hints_label(hints, loc)]
    payload = _base_payload(
        kind=KIND_ALPHABETTY,
        number=number,
        locale=locale,
        date_value=date_value,
        elapsed_seconds=elapsed_seconds,
        headline_style=headline_style,
        brand_host=brand_host,
        extra_stats=extra,
        seed=number,
    )
    payload['attempts'] = max(0, int(attempts or 0))
    payload['attempts_word'] = _attempts_word(payload['attempts'], loc)
    payload['hint_count'] = max(0, int(hints or 0))
    if variant is None:
        try:
            variant = int(payload['seed']) % 3
        except (TypeError, ValueError):
            variant = 0
    payload['variant'] = int(variant) % 3
    return payload


# Start/end are public given words. Middle placeholders make a renderer leak obvious.
_SYNTHETIC_LADDER = {
    'lengths': [5, 9, 7, 4, 6, 14, 9, 9, 7, 3, 6, 4, 5],
    'hints': ['hint'] * 12,
    'words': [
        'ПАРИЖ', 'XXXXXXXXX', 'XXXXXXX', 'XXXX', 'XXXXXX', 'XXXXXXXXXXXXXX',
        'XXXXXXXXX', 'XXXXXXXXX', 'XXXXXXX', 'XXX', 'XXXXXX', 'XXXX', 'ДАКАР',
    ],
}
_SYNTHETIC_SALAD_GRID = list('АБВГДЕЖЗИЙКЛМНОП')


def synthetic_preview_payloads() -> list[dict[str, Any]]:
    """Marked synthetic results for visual QA. No production user data."""
    from games.raddle import default_raddle_state, parse_raddle_data
    from games.word_salad import default_state as salad_default_state

    parsed = parse_raddle_data(_FakeTask(_SYNTHETIC_LADDER))
    n = parsed['n_words']
    perfect_state = default_raddle_state(n)
    perfect_state['solved_indices'] = list(range(n))
    messy_state = dict(perfect_state)
    messy_state['assist_tier'] = {'2': 1, '4': 2, '7': 1, '10': 2}

    salad_words = ['МОСКВА', 'ПАРИЖ', 'РИМ', 'ОСЛО', 'КИЕВ', 'МИНСК']
    salad_perfect = salad_default_state()
    salad_perfect['solved_indices'] = list(range(len(salad_words)))
    salad_messy = dict(salad_perfect)
    salad_messy['hint_counts'] = {1: 1, 3: 2, 5: 1}

    date_value = date(2026, 9, 3)
    items = [
        build_ladder_share_payload(
            parsed=parsed,
            state=perfect_state,
            number=46,
            date_value=date_value,
            elapsed_seconds=272,
            locale='ru',
        ),
        build_ladder_share_payload(
            parsed=parsed,
            state=messy_state,
            number=46,
            date_value=date_value,
            elapsed_seconds=512,
            locale='ru',
        ),
        build_salad_share_payload(
            words=salad_words,
            state=salad_perfect,
            number=23,
            date_value=date_value,
            elapsed_seconds=377,
            locale='ru',
            theme='Столицы',
            grid=_SYNTHETIC_SALAD_GRID,
        ),
        build_salad_share_payload(
            words=salad_words,
            state=salad_messy,
            number=23,
            date_value=date_value,
            elapsed_seconds=541,
            locale='ru',
            theme='Столицы',
            grid=_SYNTHETIC_SALAD_GRID,
        ),
        build_alphabetty_share_payload(
            number=31,
            date_value=date_value,
            elapsed_seconds=128,
            attempts=6,
            hints=0,
            locale='ru',
            variant=0,
        ),
        build_alphabetty_share_payload(
            number=31,
            date_value=date_value,
            elapsed_seconds=248,
            attempts=1,
            hints=2,
            locale='ru',
            variant=1,
        ),
        build_alphabetty_share_payload(
            number=32,
            date_value=date_value,
            elapsed_seconds=188,
            attempts=3,
            hints=1,
            locale='ru',
            variant=2,
        ),
        build_ladder_share_payload(
            parsed=parsed,
            state=perfect_state,
            number=46,
            date_value=date_value,
            elapsed_seconds=272,
            locale='en',
        ),
        build_salad_share_payload(
            words=salad_words,
            state=salad_perfect,
            number=23,
            date_value=date_value,
            elapsed_seconds=377,
            locale='en',
            grid=_SYNTHETIC_SALAD_GRID,
        ),
        build_alphabetty_share_payload(
            number=31,
            date_value=date_value,
            elapsed_seconds=128,
            attempts=6,
            hints=0,
            locale='en',
            variant=0,
        ),
    ]
    for payload in items:
        payload['synthetic'] = True
    return items


class _FakeTask:
    """Minimal task-shaped object for parse_raddle_data in synthetic fixtures."""

    task_type = 'raddle'

    def __init__(self, data: dict):
        self.checker_data = json.dumps(data, ensure_ascii=False)
        self.answer = ''


def publish_date_for(game, number) -> Optional[date]:
    if game is None or number is None:
        return None
    try:
        from games.daily_section import publish_at_for
        pub_at = publish_at_for(game, number)
    except Exception:
        return None
    if pub_at is None:
        return None
    return pub_at.date()


def elapsed_seconds_for(*, game, task, user=None, anon_key=None, attempts=None) -> int:
    from games.daily_timing import canonical_elapsed_seconds

    return canonical_elapsed_seconds(
        game=game,
        task_group=getattr(task, 'task_group', None),
        user=user,
        anon_key=anon_key,
        attempts=attempts or [],
    )


def attach_ladder_share_card(
    ui: dict[str, Any],
    *,
    parsed: dict[str, Any],
    state: dict[str, Any] | None,
    hint_attempts=None,
    game=None,
    task=None,
    placement=None,
    user=None,
    anon_key=None,
    attempts=None,
    locale: str = 'ru',
) -> dict[str, Any]:
    if not ui or not ui.get('is_complete'):
        return ui
    number = getattr(placement, 'number', None)
    elapsed = elapsed_seconds_for(
        game=game, task=task, user=user, anon_key=anon_key, attempts=attempts,
    ) if ui.get('elapsed_label') else None
    payload = build_ladder_share_payload(
        parsed=parsed,
        state=state,
        hint_attempts=hint_attempts,
        number=number,
        date_value=publish_date_for(game, number),
        elapsed_seconds=elapsed,
        locale=locale,
    )
    ui['share_card'] = payload
    ui['share_card_json'] = dumps_payload(payload)
    return ui


def attach_salad_share_card(
    ui: dict[str, Any],
    *,
    words: list,
    state: dict[str, Any] | None,
    game=None,
    task=None,
    placement=None,
    user=None,
    anon_key=None,
    attempts=None,
    locale: str = 'ru',
    grid=None,
) -> dict[str, Any]:
    if not ui or not ui.get('is_complete'):
        return ui
    from games.word_salad import theme_from_text

    number = getattr(placement, 'number', None)
    elapsed = elapsed_seconds_for(
        game=game, task=task, user=user, anon_key=anon_key, attempts=attempts,
    ) if ui.get('elapsed_label') else None
    payload = build_salad_share_payload(
        words=words,
        state=state,
        number=number,
        date_value=publish_date_for(game, number),
        elapsed_seconds=elapsed,
        locale=locale,
        theme=theme_from_text(getattr(task, 'text', None)),
        grid=grid,
    )
    ui['share_card'] = payload
    ui['share_card_json'] = dumps_payload(payload)
    return ui
