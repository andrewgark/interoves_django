"""A daily puzzle written out for the admin chat, answers under spoilers.

Shared by the 22:00 review of tomorrow's editions and by the alerts about
puzzles players offer, so both read the same way.
"""

from __future__ import annotations

import html

from django.utils.html import strip_tags

from games.ops.telegram.bot.people import person_name, user_html

AUTHOR_TAG = 'author'


def escape(value) -> str:
    return html.escape(str(value or ''), quote=False)


def plain_text(value) -> str:
    return ' '.join(html.unescape(strip_tags(str(value or ''))).split())


def spoiler(value) -> str:
    return '<tg-spoiler>{}</tg-spoiler>'.format(escape(value))


def group_tasks(task_group_id, preferred_type: str = ''):
    from games.models import Task

    tasks = Task.objects.visible().filter(task_group_id=task_group_id).order_by('number', 'id')
    if preferred_type:
        preferred = [task for task in tasks if task.task_type == preferred_type]
        if preferred:
            return preferred
    return list(tasks)


def first_task(task_group_id, preferred_type: str):
    tasks = group_tasks(task_group_id, preferred_type)
    return tasks[0] if tasks else None


def ladder_lines(task) -> tuple[list[str], bool]:
    from games.puzzles.raddle.engine import parse_raddle_data

    parsed = parse_raddle_data(task)
    if not parsed:
        return ['⚠️ Не удалось разобрать данные лесенки.'], False
    words = parsed.get('words') or []
    hints = parsed.get('hints') or []
    lines = []
    intro = plain_text(task.text)
    if intro:
        lines.append('Вводная: {}'.format(escape(intro)))
    lines.append('<b>Цепочка:</b>')
    if words:
        lines.append('Старт: {}'.format(spoiler(words[0])))
    for index, hint in enumerate(hints, start=1):
        answer = words[index] if index < len(words) else '⚠️ нет ответа'
        lines.append('{}. {} → {}'.format(index, escape(hint), spoiler(answer)))
    return lines, bool(words) and len(words) == len(hints) + 1


def alphabetty_lines(task) -> tuple[list[str], bool]:
    answer = (task.answer or task.checker_data or '').strip().splitlines()
    answer = answer[0].strip() if answer else ''
    return ['Загаданное слово: {}'.format(spoiler(answer or '⚠️ не заполнено'))], bool(answer)


def salad_lines(task) -> tuple[list[str], bool]:
    from games.puzzles.salad.rules import format_grid_text, parse_task_data, theme_from_text

    try:
        grid, words = parse_task_data(task.checker_data, task.answer or '')
    except ValueError:
        return ['⚠️ Не удалось разобрать сетку и ответы салатика.'], False
    lines = []
    theme = theme_from_text(task.theme)
    if theme:
        lines.append('Тема: {}'.format(escape(theme)))
    lines.extend([
        '<pre>{}</pre>'.format(escape(format_grid_text(grid))),
        'Ответы ({}): {}'.format(len(words), spoiler(' · '.join(words) or '⚠️ не заполнены')),
    ])
    return lines, len(grid) == 16 and bool(words)


def censorly_lines(task) -> tuple[list[str], bool]:
    from games.puzzles.censorly.play import puzzle_from_task

    payload = puzzle_from_task(task) or {}
    title = plain_text(payload.get('wiki_title'))
    body = plain_text(payload.get('body_text'))
    if not title:
        return ['⚠️ Не удалось получить название статьи.'], False
    lines = ['Статья: {}'.format(spoiler(title))]
    if body:
        lines.append('Длина текста: {} слов'.format(len(body.split())))
    return lines, bool(title and body and payload.get('title_tokens') is not None)


PUZZLE_LINES = {
    'raddle': ladder_lines,
    'alphabetty': alphabetty_lines,
    'word_salad': salad_lines,
    'censorly': censorly_lines,
}


def _offer_for_group(task_group_id, link_id=None):
    from django.db.models import Q

    from games.models import AlphabettyOffer, CensorlyOffer, LadderOffer, WordSaladOffer

    for model, by_group in (
        (LadderOffer, Q(task_group_id=task_group_id)),
        (AlphabettyOffer, Q(task_group_id=task_group_id)),
        (WordSaladOffer, Q(task_group_id=task_group_id)),
        (CensorlyOffer, Q(party__task_group_id=task_group_id)),
    ):
        query = by_group | Q(accepted_link_id=link_id) if link_id else by_group
        offer = model.objects.filter(query).select_related('user', 'user__profile').first()
        if offer is not None:
            return offer
    return None


def author_html(*, task_group_id, link_id=None, task=None) -> str:
    """The person behind an edition: offer author, then group authors, then the signature tag."""
    signature = str(((getattr(task, 'tags', None) or {}).get(AUTHOR_TAG)) or '').strip()
    offer = _offer_for_group(task_group_id, link_id)
    users = []
    if offer is not None:
        users = [offer.user]
        signature = signature or str(getattr(offer, 'author', '') or '').strip()
    else:
        from games.models import TaskGroup

        group = TaskGroup.objects.filter(pk=task_group_id).prefetch_related('authors__user').first()
        if group is not None:
            users = [profile.user for profile in group.authors.all()]
    if users:
        label = ', '.join(user_html(user) for user in users)
        if signature and signature not in {person_name(user) for user in users}:
            label += ' · подпись «{}»'.format(escape(signature))
        return label
    if signature:
        return escape(signature)
    return '—'
