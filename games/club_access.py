"""Canonical Club entitlement and daily-archive access."""
from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from games.daily_section import current_number_for, is_scheduled_game
from games.tribute_config import club_archive_gating_enabled
from games.word_salad import WORD_SALAD_GAME_ID

CLUB_ARCHIVE_GAME_IDS = frozenset({
    'ladder',
    'alphabetty',
    WORD_SALAD_GAME_ID,
    'week_task',
})


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


def scheduled_number_requires_club(game, number, *, now=None) -> bool:
    """True for an official published-but-not-current daily/weekly archive item."""
    if not club_archive_gating_enabled():
        return False
    if not is_club_archive_game(getattr(game, 'id', None)):
        return False
    if _numeric_number(number) is None:
        return False
    if not is_scheduled_game(getattr(game, 'id', None)):
        return False
    return not is_current_scheduled_number(game, number, now=now)


def user_can_access_scheduled_number(user, game, number, *, now=None) -> bool:
    if not scheduled_number_requires_club(game, number, now=now):
        return True
    if getattr(user, 'is_staff', False):
        return True
    return has_club_access(user, now=now)


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
    if resolved is None and task is not None:
        resolved = club_archive_number_for_task(game, task)
    if resolved is None:
        return None
    if user_can_access_scheduled_number(request.user, game, resolved):
        return None
    return club_archive_locked_response(request, game, resolved, json_mode=json_mode)
