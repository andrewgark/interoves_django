"""Разделы консоли для простых ежедневных игр: адвент-календарь и криптики."""

from __future__ import annotations

from django.utils.html import escape

from games.console.games.simple_daily import (
    DayField,
    SimpleDailyConsole,
    SimpleDailyError,
    SimpleDailySection,
    TaskContent,
)
from games.console.http.constants import ADVENT_CONSOLE_GROUP, CRYPTIC_CONSOLE_GROUP
from games.daily.catalog.cryptic import (
    PREVIEW_FIELDS,
    answer_error,
    cryptic_editor_preview,
    enumeration_for,
    task_enumeration,
)
from games.daily.page.section import ADVENT_SCHEDULE, CRYPTIC_SCHEDULE
from games.puzzles.cryptic.explain import ExplainError, validate as validate_explanation
from games.puzzles.cryptic.hints import CRYPTIC_POINTS, sync_cryptic_hints

# Authors recorded on every new day: advent tasks are Ира Броварь's (prod
# user 16), cryptics are the site author's (Андрей Гаркавый, user 1).
IRA_BROVAR_USER_ID = 16
DAILY_DEFAULT_AUTHOR_USER_ID = 1

# --- Адвент-календарь: HTML-условие, ответы и промежуточные ответы -------------


def _lines(raw) -> list[str]:
    return [line.strip() for line in str(raw or '').replace('\r', '').split('\n') if line.strip()]


def _advent_to_task(fields: dict[str, str]) -> TaskContent:
    answers = _lines(fields.get('answers'))
    if not answers:
        raise SimpleDailyError('Нужен хотя бы один принимаемый ответ')
    keep_going = [line.lstrip('?').strip() for line in _lines(fields.get('keep_going'))]
    checker_data = '\n'.join(answers + ['? ' + line for line in keep_going if line])
    return TaskContent(
        text=str(fields.get('text') or ''),
        checker_data=checker_data,
        answer=str(fields.get('answer') or '').strip() or answers[0],
        answer_comment=str(fields.get('answer_comment') or '').strip(),
    )


def _advent_from_task(task) -> dict[str, str]:
    answers, keep_going = [], []
    for line in _lines(task.checker_data):
        if line.startswith('?'):
            keep_going.append(line[1:].strip())
        else:
            answers.append(line)
    shown = (task.answer or '').strip()
    return {
        'text': task.text or '',
        'answers': '\n'.join(answers),
        'keep_going': '\n'.join(keep_going),
        'answer': '' if answers and shown == answers[0] else shown,
        'answer_comment': task.answer_comment or '',
    }


def _advent_summary(task) -> str:
    answers = [line for line in _lines(task.checker_data) if not line.startswith('?')]
    return 'Ответ: {}'.format(answers[0]) if answers else ''


ADVENT_SECTION = SimpleDailySection(
    game_id=ADVENT_SCHEDULE.game_id,
    schedule=ADVENT_SCHEDULE,
    console_group=ADVENT_CONSOLE_GROUP,
    title='New Year Hunt',
    default_author_user_id=IRA_BROVAR_USER_ID,
    default_author_label='Ира Броварь',
    checker_id='puzzlehunt',
    day_name='День {n}',
    lead=(
        'Одно задание в день. День №N выходит в полночь по Москве: первый день + (N−1) дней. '
        'Вышедшие дни зафиксированы: их нельзя переставлять или удалять, но текст и ответы '
        'можно поправить.'
    ),
    fields=(
        DayField('text', 'Условие (HTML)', kind='html', rows=10, media=True),
        DayField(
            'answers', 'Принимаемые ответы, каждый с новой строки', kind='textarea',
            required=True, hint='Регистр, пробелы и знаки препинания при проверке не важны.',
        ),
        DayField(
            'keep_going', 'Промежуточные ответы, каждый с новой строки', kind='textarea',
            hint=(
                'На них игрок увидит «Продолжайте!». Свой текст можно написать после «=>»: '
                'полпути => Вы на верном пути, осталось одно слово'
            ),
        ),
        DayField(
            'answer', 'Ответ, который покажем после решения', placeholder='Первый из принимаемых, если пусто',
        ),
        DayField('answer_comment', 'Комментарий к ответу (HTML)', kind='textarea', media=True),
    ),
    to_task=_advent_to_task,
    from_task=_advent_from_task,
    summary=_advent_summary,
)

# --- Криптики: подсказка, ответ и маска ответа -------------------------------


def _cryptic_to_task(fields: dict[str, str]) -> TaskContent:
    clue = str(fields.get('clue') or '').strip()
    answer = str(fields.get('answer') or '').strip()
    if not clue:
        raise SimpleDailyError('Нужна подсказка')
    if not answer:
        raise SimpleDailyError('Нужен ответ')
    error = answer_error(answer, fields.get('enumeration'))
    if error:
        raise SimpleDailyError(error)
    enumeration = str(fields.get('enumeration') or '').strip() or enumeration_for(answer)
    markup = str(fields.get('markup') or '').strip()
    formula = str(fields.get('formula') or '').strip()
    try:
        validate_explanation(clue=clue, answer=answer, markup=markup, formula=formula)
    except ExplainError as exc:
        raise SimpleDailyError(str(exc)) from exc
    text = (
        '<p class="cryptic-clue">{clue} '
        '<span class="cryptic-clue__len" data-cryptic-enum="{enum}">({enum})</span></p>'
    ).format(clue=escape(clue), enum=escape(enumeration))
    return TaskContent(
        text=text,
        checker_data=answer,
        answer=answer,
        answer_comment=str(fields.get('explanation') or '').strip(),
        tags={
            'cryptic_clue': clue,
            'cryptic_enumeration': enumeration,
            'cryptic_markup': markup,
            'cryptic_formula': formula,
        },
    )


def _cryptic_from_task(task) -> dict[str, str]:
    tags = task.tags or {}
    answer = (task.answer or task.checker_data or '').strip()
    enumeration = str(tags.get('cryptic_enumeration') or '')
    return {
        'clue': str(tags.get('cryptic_clue') or ''),
        'answer': answer,
        'enumeration': '' if enumeration == enumeration_for(answer) else enumeration,
        'markup': str(tags.get('cryptic_markup') or ''),
        'formula': str(tags.get('cryptic_formula') or ''),
        'explanation': task.answer_comment or '',
    }


def _cryptic_preview(fields: dict[str, str]) -> dict:
    return cryptic_editor_preview(**{key: fields.get(key) for key in PREVIEW_FIELDS})


def _cryptic_summary(task) -> str:
    answer = (task.answer or '').strip()
    return 'Ответ: {} ({})'.format(answer, task_enumeration(task)) if answer else ''


CRYPTIC_SECTION = SimpleDailySection(
    game_id=CRYPTIC_SCHEDULE.game_id,
    schedule=CRYPTIC_SCHEDULE,
    console_group=CRYPTIC_CONSOLE_GROUP,
    title='Криптики',
    default_author_user_id=DAILY_DEFAULT_AUTHOR_USER_ID,
    default_author_label='Андрей Гаркавый',
    checker_id='equals_with_possible_spaces',
    day_name='Криптик {n}',
    lead=(
        'Один криптик в день. Криптик №N выходит в полночь по Москве: первый день + (N−1) дней. '
        'Пока первый день не задан, раздел на сайте показывает «Скоро!». '
        'Вышедшие криптики нельзя переставлять или удалять, но править можно.'
    ),
    fields=(
        DayField(
            'clue', 'Подсказка', kind='textarea', rows=3, required=True,
            hint='Маску ответа в скобках допишем сами.',
        ),
        DayField(
            'answer', 'Ответ', required=True,
            hint='Регистр, пробелы и знаки препинания при проверке не важны.',
        ),
        DayField(
            'enumeration', 'Маска ответа', placeholder='Посчитаем по ответу, если пусто',
            hint='Например 6, 3-4 или 5, 4.',
        ),
        DayField(
            'markup', 'Разбор: разметка', kind='textarea', rows=2, mono=True,
            placeholder='*Пьяный* (мужчина же) | {сокровище}',
        ),
        DayField(
            'formula', 'Разбор: формула', mono=True, placeholder='анаг[Пьяный](МУЖЧИНА ЖЕ)',
            hint=(
                'Разбор покажем после решения, а до решения — две подсказки по −1 баллу из {}: '
                'черта с определением, затем вся разметка. Пустой разбор можно (тогда без подсказок), '
                'заполненный должен сходиться.'
            ).format(CRYPTIC_POINTS),
        ),
        DayField('explanation', 'Комментарий под разбором (HTML)', kind='textarea'),
    ),
    to_task=_cryptic_to_task,
    from_task=_cryptic_from_task,
    summary=_cryptic_summary,
    preview=_cryptic_preview,
    preview_title='Так увидят разбор',
    help_template='site/_cryptic_explain_help.html',
    help_label='Как записывать разбор',
    offers_template='support/_cryptic_offers.html',
    day_points=CRYPTIC_POINTS,
    after_save=sync_cryptic_hints,
)

SECTIONS: dict[str, SimpleDailySection] = {
    ADVENT_SECTION.game_id: ADVENT_SECTION,
    CRYPTIC_SECTION.game_id: CRYPTIC_SECTION,
}


def console_for(game_id: str) -> SimpleDailyConsole:
    return SimpleDailyConsole(SECTIONS[game_id])
