"""Общие хелперы страниц Алфавитки и Цензурки.

Лесенка и Салатик рисуются общим play page и этот модуль не используют.
Счётчик подсказок читается модулем самой игры: у Алфавитки старый
``hint_prefix`` тоже считается подсказкой.
"""

from __future__ import annotations

from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone

from games.daily.registry import get_daily_game
from games.identity import has_profile
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup
from games.ops.analytics.identity import gameplay_anon_key
from games.sections.paths import section_hub_path, section_play_path
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
