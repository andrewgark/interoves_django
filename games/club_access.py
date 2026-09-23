"""Canonical Club entitlement and daily-archive access."""
from __future__ import annotations

import re

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from games.daily_section import current_number_for
from games.tribute_config import club_archive_gating_enabled
from games.word_salad import WORD_SALAD_GAME_ID

CLUB_ARCHIVE_GAME_IDS = frozenset({
    'ladder',
    'alphabetty',
    WORD_SALAD_GAME_ID,
    'week_task',
    'replacements',
    'walls',
    'palindromes',
})
# Without a club subscription, the newest this many published numbers stay free.
FREE_ARCHIVE_COUNT = 7
DESYATOCHKA_ID_RE = re.compile(r'^des(\d+)$')


def get_club_subscription(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    from games.models import ClubSubscription

    return ClubSubscription.objects.filter(user=user).first()


def has_club_access(user, *, now=None) -> bool:
    """True when the authenticated user has paid Club access at `now`."""
    now = now or timezone.now()
    subscription = get_club_subscription(user)
    if subscription is None:
        return False
    return subscription.grants_access(now)


def is_club_archive_game(game_id) -> bool:
    return str(game_id or '') in CLUB_ARCHIVE_GAME_IDS


def is_desyatka_game(game) -> bool:
    return (
        getattr(game, 'project_id', None) == 'main'
        and DESYATOCHKA_ID_RE.fullmatch(str(getattr(game, 'id', '') or '')) is not None
    )


def desyatka_number(game) -> int | None:
    if not is_desyatka_game(game):
        return None
    return int(DESYATOCHKA_ID_RE.fullmatch(str(game.id)).group(1))


def _latest_desyatka_numbers() -> set[int]:
    from games.models import Game

    numbers = []
    for game_id in Game.objects.filter(
        project_id='main', id__startswith='des',
    ).values_list('id', flat=True):
        match = DESYATOCHKA_ID_RE.fullmatch(str(game_id))
        if match:
            numbers.append(int(match.group(1)))
    return set(sorted(numbers, reverse=True)[:FREE_ARCHIVE_COUNT])


def desyatka_requires_club(game) -> bool:
    if not club_archive_gating_enabled() or not is_desyatka_game(game):
        return False
    number = desyatka_number(game)
    return number is not None and number not in _latest_desyatka_numbers()


def user_can_access_desyatka(user, game, *, now=None) -> bool:
    if not desyatka_requires_club(game):
        return True
    if getattr(user, 'is_staff', False):
        return True
    return has_club_access(user, now=now)


def is_current_scheduled_number(game, number, *, now=None) -> bool:
    current = current_number_for(game, now)
    if current is None:
        return True
    try:
        return int(number) == int(current)
    except (TypeError, ValueError):
        return False


def _numeric_number(number) -> int | None:
    try:
        value = int(number)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def is_within_free_archive_window(game, number, *, now=None) -> bool:
    """True for the latest FREE_ARCHIVE_COUNT published numbers in a section."""
    current = current_number_for(game, now)
    if current is None and is_club_archive_game(getattr(game, 'id', None)):
        from games.models import GameTaskGroup
        numbers = [
            value for value in (
                _numeric_number(raw)
                for raw in GameTaskGroup.objects.filter(game=game).values_list('number', flat=True)
            ) if value is not None
        ]
        current = max(numbers) if numbers else None
    if current is None:
        return True
    n = _numeric_number(number)
    if n is None:
        return False
    oldest_free = int(current) - FREE_ARCHIVE_COUNT + 1
    return oldest_free <= n <= int(current)


def scheduled_number_requires_club(game, number, *, now=None) -> bool:
    """True for official archive items older than the free rolling window."""
    if not club_archive_gating_enabled():
        return False
    if not is_club_archive_game(getattr(game, 'id', None)):
        return False
    if _numeric_number(number) is None:
        return False
    return not is_within_free_archive_window(game, number, now=now)


def user_can_access_scheduled_number(user, game, number, *, now=None) -> bool:
    if desyatka_requires_club(game):
        return user_can_access_desyatka(user, game, now=now)
    if not scheduled_number_requires_club(game, number, now=now):
        return True
    if getattr(user, 'is_staff', False):
        return True
    if has_club_access(user, now=now):
        return True
    # A solved archive item remains available to its solver after the free
    # rolling window expires. This is intentionally checked only when gating
    # is enabled, so the old behaviour remains byte-for-byte unchanged while
    # the feature flag is off.
    if getattr(user, 'is_authenticated', False):
        from games.models import GameTaskGroup, PlayerCompletedGame
        placement = GameTaskGroup.objects.filter(game=game, number=number).only('task_group_id').first()
        if placement and PlayerCompletedGame.objects.filter(
            user=user, game=game, task_group_id=placement.task_group_id,
            result=PlayerCompletedGame.RESULT_SOLVED,
        ).exists():
            return True
    return False


def club_archive_number_for_task(game, task):
    if game is None or task is None or not is_club_archive_game(getattr(game, 'id', None)):
        return None
    from games.models import GameTaskGroup

    link = (
        GameTaskGroup.objects.filter(game=game, task_group_id=task.task_group_id)
        .only('number')
        .first()
    )
    if link is None:
        return None
    return _numeric_number(link.number)


def user_can_access_task_archive(user, game, task, *, now=None) -> bool:
    if desyatka_requires_club(game):
        return user_can_access_desyatka(user, game, now=now)
    number = club_archive_number_for_task(game, task)
    if number is None:
        return True
    return user_can_access_scheduled_number(user, game, number, now=now)


def club_archive_locked_response(request, game, number, *, json_mode=False):
    if json_mode:
        return JsonResponse({'status': 'error', 'reason': 'club_required'}, status=403)
    from games.views.new_ui import NEW_UI_PROJECT, _project_urls_context

    return render(
        request,
        'ui/club_archive_locked.html',
        {
            'game': game,
            'number': number,
            'page_title': 'Архив',
            'robots_noindex': True,
            **_project_urls_context(NEW_UI_PROJECT),
        },
        status=403,
    )


def reject_if_club_archive_blocked(request, game, *, number=None, task=None, json_mode=False):
    """Return an HTTP response when Club is required, otherwise None."""
    resolved = number
    if desyatka_requires_club(game):
        if user_can_access_desyatka(request.user, game):
            return None
        return club_archive_locked_response(
            request, game, desyatka_number(game), json_mode=json_mode,
        )
    if resolved is None and task is not None:
        resolved = club_archive_number_for_task(game, task)
    if resolved is None:
        return None
    if task is not None:
        allowed = user_can_access_task_archive(request.user, game, task)
    else:
        allowed = user_can_access_scheduled_number(request.user, game, resolved)
    if allowed:
        return None
    return club_archive_locked_response(request, game, resolved, json_mode=json_mode)
