"""Session-backed play mode selection and tournament play-mode policy."""

from games.club.access.game import game_has_ended, game_is_going_now
from games.identity import has_profile, has_team

# Личный режим отключаем только в турнирных играх проекта «Десяточки».
# У Game.is_tournament по умолчанию True — если ориентироваться только на него,
# личный режим блокируется и в разделах (sections), где он как раз нужен.
_PERSONAL_MODE_LOCK_PROJECT_IDS = frozenset({'main'})


def session_play_mode_key(project_id):
    return 'play_mode_{}'.format(project_id or 'main')


def default_play_mode(project_id, *, sections_project_id='sections'):
    return 'personal' if project_id == sections_project_id else 'team'


def get_play_mode(request, project_id, *, sections_project_id='sections'):
    key = session_play_mode_key(project_id)
    mode = request.session.get(key)
    if mode not in ('team', 'personal'):
        mode = default_play_mode(
            project_id,
            sections_project_id=sections_project_id,
        )
    return mode, key


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
