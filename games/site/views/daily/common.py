"""Общие хелперы страниц Алфавитки и Цензурки.

Лесенка и Салатик рисуются общим play page и этот модуль не используют.
Счётчик подсказок читается модулем самой игры: у Алфавитки старый
``hint_prefix`` тоже считается подсказкой.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone

from games.daily.registry import get_daily_game
from games.identity import has_profile
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Task
from games.ops.analytics.identity import gameplay_anon_key
from games.sections.hub import create_game_link_context
from games.sections.paths import section_hub_path, section_play_path, section_results_path
from games.site.pages import NEW_UI_SECTIONS_PROJECT
from games.site.views.daily.hub import can_access_daily_game
from games.task_group.likes_meta import likes_meta_by_task_id


def share_host(request) -> str:
    return request.get_host() or 'interoves.com'


def get_section_game(game_id) -> Game | None:
    return Game.objects.filter(
        id=game_id,
        project_id=NEW_UI_SECTIONS_PROJECT,
    ).first()


def published_numbers(game, *, digits_only=False):
    """Номера уже вышедших выпусков.

    ``digits_only`` — расхождение Цензурки: ``int(' 1')`` и ``int('+1')``
    проходят, а ``str.isdigit()`` — нет. ``filter_published`` и так
    отбрасывает share hash случайной игры.
    """
    definition = get_daily_game(game.id)
    if definition is None:
        return set()
    links = GameTaskGroup.objects.filter(game=game)
    numbers = {
        link.number
        for link in definition.schedule.filter_published(links, game)
    }
    if digits_only:
        return {number for number in numbers if str(number).isdigit()}
    return numbers


def resolve_actor(request, *, body=None):
    """Logged-in user, otherwise the canonical browser anonymous cookie.

    ``body`` is accepted so callers can keep passing the parsed JSON; the
    actor is taken from the session and the anonymous cookie, not from it.
    """
    if request.user.is_authenticated:
        return request.user, None
    anon_key = gameplay_anon_key(request)
    if anon_key:
        return None, str(anon_key)
    return None, None


def _hint_readers(task):
    task_type = getattr(task, 'task_type', None)
    if task_type == 'alphabetty':
        from games.puzzles.alphabetty.play import hint_count, load_state, ru_hint_word
        return hint_count, load_state, ru_hint_word
    if task_type == 'censorly':
        from games.puzzles.censorly.play import hint_count, load_state, ru_hint_word
        return hint_count, load_state, ru_hint_word
    return None


def _hints_taken(*, game, task, user, anon_key) -> int:
    readers = _hint_readers(task)
    if readers is None:
        return 0
    hint_count, load_state, _ru_hint_word = readers
    qs = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general', replay_slot__isnull=True,
    )
    if user is not None:
        qs = qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        qs = qs.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    else:
        return 0
    row = qs.first()
    if row is None:
        return 0
    return hint_count(load_state(row.state))


def _hints_label(task, hints_n: int) -> str:
    if hints_n <= 0:
        return ''
    readers = _hint_readers(task)
    if readers is None:
        return ''
    _hint_count, _load_state, ru_hint_word = readers
    return f'{hints_n} {ru_hint_word(hints_n)}'


def meta_context(request, *, game, task, user, anon_key, placement=None):
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    ai = Attempt.manager.get_attempts_info(
        team=None,
        task=task,
        mode=mode,
        user=user,
        anon_key=anon_key,
        game=game,
    )
    hints_n = _hints_taken(game=game, task=task, user=user, anon_key=anon_key)
    difficulty = None
    if placement is not None:
        from games.daily.difficulty.core import get_game_difficulty
        difficulty = get_game_difficulty(placement)
    label = _hints_label(task, hints_n)
    return {
        'game': game,
        'task': task,
        'ai': ai,
        'mode': mode,
        'base_max': task.get_points(),
        'wall_max_title': '',
        'task_ui': {
            'show_attempts': False,
            'show_answer': False,
            'hints_label': label,
        },
        'is_daily_single_task': True,
        'difficulty': difficulty,
        'has_profile_user': has_profile(request.user),
        'user': request.user,
        'likes_meta_by_task_id': likes_meta_by_task_id(
            [task], team=None, user=user, anon_key=anon_key,
        ),
        'hints_taken': hints_n,
        'hints_label': label,
    }


def meta_bar_html(request, *, game, task, user, anon_key, placement=None) -> str:
    return render_to_string(
        'site/task-content/task-meta-bar.html',
        meta_context(
            request,
            game=game,
            task=task,
            user=user,
            anon_key=anon_key,
            placement=placement,
        ),
        request=request,
    )


def with_meta_bar(
    payload: dict,
    request,
    *,
    game,
    task,
    user,
    anon_key,
    placement=None,
) -> dict:
    out = dict(payload)
    out['meta_bar_html'] = meta_bar_html(
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=placement,
    )
    return out


def preview_denied(request, game):
    if can_access_daily_game(request, game):
        return None
    return JsonResponse({'status': 'error', 'error': 'not found'}, status=404)


def _require_daily_page(request, game_id):
    game = get_section_game(game_id)
    if not game:
        raise Http404()
    if not can_access_daily_game(request, game):
        raise Http404()
    definition = get_daily_game(game_id)
    if definition is None:
        raise Http404()
    return game, definition


def today_redirect(request, game_id, *, use_published_cta=False, digits_only=False):
    """Куда ведёт /today/.

    ``use_published_cta`` — как кнопка хаба: сегодняшний номер, если выпуск
    уже вышел, иначе последний вышедший. Без флага — календарный номер на
    сегодня, даже если выпуска с таким номером ещё нет. Так расходятся
    Алфавитка и Цензурка.
    """
    game, definition = _require_daily_page(request, game_id)
    if use_published_cta:
        hub = definition.schedule.hub_context(
            game,
            published_numbers=published_numbers(game, digits_only=digits_only),
        )
        number = hub.get(f'{game_id}_cta_number')
    else:
        number = definition.schedule.current_number(game)
    if not number:
        return redirect(section_hub_path(game_id))
    return redirect(section_play_path(game_id, number))


def last_redirect(request, game_id, *, digits_only=False):
    """Редирект на последнюю вышедшую игру раздела, иначе на хаб."""
    game, _definition = _require_daily_page(request, game_id)
    ints = []
    for number in published_numbers(game, digits_only=digits_only):
        try:
            ints.append(int(number))
        except (TypeError, ValueError):
            continue
    if not ints:
        return redirect(section_hub_path(game_id))
    return redirect(section_play_path(game_id, max(ints)))


def random_game_redirect(request, *, game_id, create, failures, play_url):
    """Создать случайную игру и увести на неё либо обратно на хаб.

    Доступ в клуб и отсутствие игры проверяет страница. ``failures`` —
    пары (исключение, уровень messages, текст) в том порядке, в каком
    их надо показывать.
    """
    try:
        random_game = create()
    except tuple(exc_type for exc_type, _level, _text in failures) as exc:
        for exc_type, level, text in failures:
            if isinstance(exc, exc_type):
                getattr(messages, level)(request, text)
                return redirect(section_hub_path(game_id))
        raise
    return redirect(play_url(random_game))


_ARCHIVE_ROW_CLASSES = ('new-task--partial', 'new-task--solved')


def _edition_publish_at(definition, game, number):
    return definition.schedule.publish_at(game, number)


def _numbered_editions(game, *, digits_only):
    """Вышедшие номера по убыванию. Случайные игры в список не входят."""
    definition = get_daily_game(game.id)
    if definition is None:
        return None, []
    published = definition.schedule.filter_published(
        GameTaskGroup.objects.filter(game=game).select_related('task_group'),
        game,
    )
    numbered = []
    for link in GameTaskGroup.sorted_links(published, reverse=True):
        raw = str(link.number)
        if digits_only and not raw.isdigit():
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        numbered.append((number, link))
    return definition, numbered


def _archive_status_by_key(rows):
    """Календарь красит только partial и solved.

    Тот же белый список, что у хаба разделов. Срезание любого
    ``new-task--*`` превратило бы класс ``today`` в день календаря.
    """
    return {
        str(row['number']): ('solved' if row['is_solved'] else 'partial')
        for row in rows
        if row.get('row_class') in _ARCHIVE_ROW_CLASSES
    }


def daily_hub_context(
    request,
    game,
    *,
    progress_for_actor,
    page_title,
    random_button_template,
    coming_soon_text,
    rules_body_template,
    empty_text,
    digits_only=False,
):
    """Строки архива, календарь и общая шапка хаба.

    Прогресс и серия считаются по всем вышедшим выпускам. В списке
    остаётся выбранный месяц. Подписи выпуска и правила берутся из
    реестра; тексты, которые ещё живут у страницы, передаёт игра.
    """
    from games.daily.difficulty.core import get_cached_game_difficulties
    from games.daily.page.archive import (
        links_for_archive_month,
        prepare_daily_archive,
        refresh_daily_archive_context,
    )
    from games.daily.page.section import MOSCOW
    from games.daily.timing.transitions import next_daily_content_transition_for_game
    from games.site.pages.game.progress import _mark_locked_archive_rows
    from games.site.pages.hub.cards import _add_daily_completion_statuses
    from games.task_group.likes_meta import like_totals_by_task_group_id

    definition, numbered = _numbered_editions(game, digits_only=digits_only)
    if definition is None:
        raise Http404()
    publish_at_by_number = {
        number: _edition_publish_at(definition, game, number)
        for number, _link in numbered
    }
    archive_items, archive_context = prepare_daily_archive(
        request,
        numbered,
        game_id=game.id,
        publish_at_for_number=publish_at_by_number.get,
        href_for_number=lambda number: section_play_path(game.id, number),
        timezone_name=MOSCOW,
        number_for_link=lambda pair: pair[0],
        game_label=definition.title,
        calendar_id='daily-archive-{}'.format(game.id),
    )
    visible = links_for_archive_month(
        numbered,
        archive_context.get('daily_archive_selected'),
        publish_at_for_number=publish_at_by_number.get,
        number_for_link=lambda pair: pair[0],
        timezone_name=MOSCOW,
    )
    tasks_by_group = {
        task.task_group_id: task
        for task in Task.objects.filter(
            task_group_id__in=[link.task_group_id for _number, link in numbered],
            number='1',
        )
    }
    user, anon_key = resolve_actor(request)
    progress = progress_for_actor(
        game=game,
        numbers_and_tasks=[
            (number, tasks_by_group[link.task_group_id])
            for number, link in numbered
            if link.task_group_id in tasks_by_group
        ],
        user=user,
        anon_key=anon_key,
    )
    today_number = definition.schedule.current_number(game)
    difficulties = get_cached_game_difficulties([link for _number, link in visible])
    like_totals = like_totals_by_task_group_id(
        link.task_group_id for _number, link in visible
    )
    rows = []
    for number, link in visible:
        prog = progress.get(number) or {}
        published_at = publish_at_by_number.get(number)
        solved = bool(prog.get('is_solved'))
        rows.append({
            'number': number,
            'publish_date': published_at.astimezone(MOSCOW).date() if published_at else None,
            'name': f'{definition.title} №{number}',
            'play_url': section_play_path(game.id, number),
            'results_url': '{}results/'.format(section_play_path(game.id, number)),
            'is_today': today_number is not None and number == today_number,
            'is_solved': solved,
            'is_fully_solved': solved,
            'row_class': prog.get('row_class') or '',
            'progress_meta': prog.get('progress_meta') or '',
            'difficulty': difficulties.get(link.pk),
            'like_totals': like_totals.get(link.task_group_id),
        })
    _add_daily_completion_statuses(request, game, rows, numbered)
    _mark_locked_archive_rows(request, game, rows)
    archive_context = refresh_daily_archive_context(
        request,
        items=archive_items,
        game_label=definition.title,
        game_id=game.id,
        completed_keys={str(row['number']) for row in rows if row['is_solved']},
        status_by_key=_archive_status_by_key(rows),
        locked_keys={row['number'] for row in rows if row.get('is_archive_locked')},
    )
    hub = definition.schedule.hub_context(
        game,
        published_numbers=published_numbers(game, digits_only=digits_only),
    )
    team = request.user.profile.team_on if has_profile(request.user) else None
    return {
        'game': game,
        'rows': rows,
        'page_title': page_title,
        'random_button_template': random_button_template,
        'daily_hub_play_url': hub['{}_play_url'.format(game.id)],
        'daily_hub_cta_label': hub['{}_cta_label'.format(game.id)],
        'daily_hub_is_today': hub['{}_is_today'.format(game.id)],
        'daily_hub_status': hub['{}_status'.format(game.id)],
        'daily_hub_coming_soon_text': coming_soon_text,
        'rules_title': definition.title,
        'rules_storage_key': 'section_rules_seen_{}'.format(game.id),
        'rules_body_template': rules_body_template,
        'empty_text': empty_text,
        'create_link': create_game_link_context(game.id),
        'show_sections_nav': True,
        'back_url': '/',
        'section_results_url': section_results_path(game.id),
        'can_see_results': game.has_access('see_results', team=team),
        'live_next_transition_at': next_daily_content_transition_for_game(game),
        **archive_context,
    }
