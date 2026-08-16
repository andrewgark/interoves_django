from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import get_object_or_404

from games.access import game_has_ended, game_is_going_now


def get_public_task_or_404(task_id):
    """Задание для публичных вьюх: скрытые (is_removed) дают 404."""
    from games.models import Task

    return get_object_or_404(Task.objects.visible(), pk=task_id)


def redirect_to_referer(request):
    if 'HTTP_REFERER' in request.META:
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))
    if 'next' in request.POST and request.POST.get('next'):
        return HttpResponseRedirect(request.POST.get('next'))
    if 'next' in request.GET and request.GET.get('next'):
        return HttpResponseRedirect(request.GET.get('next'))
    return HttpResponseRedirect('/')


def has_profile(user):
    return user and getattr(user, 'profile', None)


def has_team(user):
    if not has_profile(user):
        return False
    user.profile.repair_primary_team()
    return user.profile.team_on_id is not None


# Личный режим отключаем только в турнирных играх проекта «Десяточки».
# У Game.is_tournament по умолчанию True — если ориентироваться только на него,
# личный режим блокируется и в разделах (sections), где он как раз нужен.
_PERSONAL_MODE_LOCK_PROJECT_IDS = frozenset({'main'})


def _authenticated_user_without_team(user, game=None):
    """Пользователь без команды, которому можно включить личный режим."""
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and has_profile(user)
        and not has_team(user)
        and game is not None
        and game.project_id in _PERSONAL_MODE_LOCK_PROJECT_IDS
        and getattr(game, 'is_tournament', False)
        and game_has_ended(game)
    )


def personal_play_mode_locked(game, user=None):
    """
    True — для этой игры недоступен личный/анонимный режим (только команда).

    После окончания Десяточки пользователь без команды может смотреть и
    решать её лично. Пока игра идёт, личный режим остаётся закрыт.

    Только десяточки (project main), флаг is_tournament и окно игры по времени:
    до старта и после end_time зачёт уже «общий», как в get_current_mode — личный режим снова можно.
    """
    if game is None:
        return False
    if game.project_id not in _PERSONAL_MODE_LOCK_PROJECT_IDS:
        return False
    if not getattr(game, 'is_tournament', False):
        return False
    return bool(game_is_going_now(game))


def effective_play_mode(play_mode, game, user=None):
    if (
        game is not None
        and game.project_id in _PERSONAL_MODE_LOCK_PROJECT_IDS
        and getattr(game, 'is_tournament', False)
        and game_has_ended(game)
        and not getattr(user, 'is_authenticated', False)
    ):
        return 'personal'
    if _authenticated_user_without_team(user, game=game):
        return 'personal'
    if personal_play_mode_locked(game):
        return 'team'
    return play_mode
