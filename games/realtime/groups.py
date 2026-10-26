"""Channel group names for live track sockets."""
import hashlib
import logging

logger = logging.getLogger('games.views.track')


def _channel_group_component(value, *, max_length: int) -> str:
    """Return a bounded ASCII component accepted by Channels.

    Team.name is the immutable primary key, but it may contain Cyrillic, spaces,
    or enough characters to push the complete group over Channels' 99-character
    limit.  Hashing only the transport representation preserves stable identity;
    revision namespaces continue to use the authoritative primary key itself.
    """
    raw = str(value)
    is_safe = (
        bool(raw)
        and raw.isascii()
        and len(raw) <= max_length
        and all(ch.isalnum() or ch in '-_.' for ch in raw)
    )
    if is_safe:
        return raw
    digest_length = max(1, max_length - 2)
    return 'h-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:digest_length]


def _game_group(game_id) -> str:
    return f'track.game.{_channel_group_component(game_id, max_length=88)}'


def _game_team_group(game_id, team_id) -> str:
    game = _channel_group_component(game_id, max_length=32)
    team = _channel_group_component(team_id, max_length=40)
    return f'track.game.{game}.team_id.{team}'


def _legacy_game_team_group(game_id, team_name_hash) -> str:
    game = _channel_group_component(game_id, max_length=32)
    team_hash = _channel_group_component(team_name_hash, max_length=50)
    return f'track.game.{game}.team.{team_hash}'


CHANNEL_GROUPS = {
    'game': _game_group,
    'game_team': _game_team_group,
    'game_team_legacy': _legacy_game_team_group,
    'user': (lambda user_id: f'track.user.{user_id}'),
    'user_game': (lambda user_id, game_id: f'track.user.{user_id}.game.{_channel_group_component(game_id, max_length=32)}'),

    # 'game_results': (lambda game_id: f'track.game.{game_id}.results'),
    # 'total_results': (lambda project_id: f'track.project.{project_id}.total_results'),
    # 'project': (lambda project_id: f'track.project.{project_id}'),
}


def game_track_namespace(game_id) -> str:
    return f'game:{game_id}'


def team_track_namespace(game_id, team_id) -> str:
    return f'game:{game_id}:team:{team_id}'


def user_track_namespace(user_id) -> str:
    return f'user:{user_id}'


def user_game_track_namespace(user_id, game_id) -> str:
    return f'user:{user_id}:game:{game_id}'
