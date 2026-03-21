from django.http import HttpResponseRedirect, HttpResponse

from games.access import game_is_going_now


def redirect_to_referer(request):
    if 'HTTP_REFERER' in request.META:
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))
    if 'next' in request.POST:
        return HttpResponseRedirect(request.POST.get('next'))
    return 


def has_profile(user):
    return user and getattr(user, 'profile', None)


def has_team(user):
    return has_profile(user) and user.profile.team_on


# Личный режим отключаем только в турнирных играх проекта «Десяточки».
# У Game.is_tournament по умолчанию True — если ориентироваться только на него,
# личный режим блокируется и в разделах (sections), где он как раз нужен.
_PERSONAL_MODE_LOCK_PROJECT_IDS = frozenset({'main'})


def personal_play_mode_locked(game):
    """
    True — для этой игры недоступен личный/анонимный режим (только команда).

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


def effective_play_mode(play_mode, game):
    if personal_play_mode_locked(game):
        return 'team'
    return play_mode
