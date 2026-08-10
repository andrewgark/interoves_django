import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID
from games.models import (
    GameTaskGroup,
    PlayerAnalyticsState,
    PlayerCompletedGame,
)


YANDEX_GOAL_SIGNUP = 'signup'
YANDEX_GOAL_GAME_START = 'game_start'
YANDEX_GOAL_GAME_COMPLETE = 'game_complete'
YANDEX_GOAL_ACTIVATED_PLAYER = 'activated_player'
YANDEX_GOAL_TICKET_CHECKOUT = 'ticket_checkout'
YANDEX_GOAL_TICKET_PURCHASE = 'ticket_purchase'

SESSION_KEY_PENDING_GOALS = 'interoves_pending_yandex_goals'

GAME_KIND_BY_ID = {
    LADDER_GAME_ID: 'ladder',
    ALPHABETTY_GAME_ID: 'alphabet',
    'replacements': 'replacement',
}


def yandex_goal_payload(goal, params=None, key=None):
    return {
        'goal': goal,
        'params': params or {},
        'key': key or goal,
    }


def queue_pending_goal(request, goal, params=None, key=None):
    if request is None or not hasattr(request, 'session'):
        return
    goals = list(request.session.get(SESSION_KEY_PENDING_GOALS, []) or [])
    payload = yandex_goal_payload(goal, params=params, key=key)
    goal_key = payload['key']
    for existing in goals:
        if isinstance(existing, dict) and existing.get('key') == goal_key:
            break
    else:
        goals.append(payload)
        request.session[SESSION_KEY_PENDING_GOALS] = goals
        request.session.modified = True


def consume_pending_goals(request):
    if request is None or not hasattr(request, 'session'):
        return []
    goals = list(request.session.pop(SESSION_KEY_PENDING_GOALS, []) or [])
    if goals:
        request.session.modified = True
    return goals


def supported_game_kind(game):
    if game is None:
        return None
    return GAME_KIND_BY_ID.get(getattr(game, 'id', None))


def _actor_kwargs(*, team=None, user=None, anon_key=None):
    if team is not None:
        return {'team': team, 'user': None, 'anon_key': None}
    if user is not None:
        return {'team': None, 'user': user, 'anon_key': None}
    if anon_key:
        return {'team': None, 'user': None, 'anon_key': str(anon_key)}
    return None


def _analytics_actor_kwargs(*, analytics_user=None, user=None, anon_key=None):
    if analytics_user is not None:
        return {'team': None, 'user': analytics_user, 'anon_key': None}
    if user is not None:
        return {'team': None, 'user': user, 'anon_key': None}
    if anon_key:
        return {'team': None, 'user': None, 'anon_key': str(anon_key)}
    return None


def _analytics_state_qs(*, team=None, user=None, anon_key=None):
    if team is not None:
        return PlayerAnalyticsState.objects.filter(team=team, user__isnull=True, anon_key__isnull=True)
    if user is not None:
        return PlayerAnalyticsState.objects.filter(user=user, team__isnull=True, anon_key__isnull=True)
    if anon_key:
        return PlayerAnalyticsState.objects.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    return PlayerAnalyticsState.objects.none()


def _completed_games_qs(*, team=None, user=None, anon_key=None):
    if team is not None:
        return PlayerCompletedGame.objects.filter(team=team, user__isnull=True, anon_key__isnull=True)
    if user is not None:
        return PlayerCompletedGame.objects.filter(user=user, team__isnull=True, anon_key__isnull=True)
    if anon_key:
        return PlayerCompletedGame.objects.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    return PlayerCompletedGame.objects.none()


def _state_complete_raddle(task, state_raw):
    if not state_raw:
        return False
    try:
        from games.raddle import load_raddle_state, parse_raddle_data

        parsed = parse_raddle_data(task)
        if not parsed:
            return False
        state = load_raddle_state(state_raw, parsed['n_words'])
        return len(set(state.get('solved_indices') or [])) >= parsed['n_words']
    except Exception:
        return False


def _state_complete_replacements(task, state_raw):
    if not state_raw:
        return False
    try:
        from games.replacements_lines import parse_replacements_lines_text

        parsed = parse_replacements_lines_text(task.text, (task.checker_data or '').strip() or None)
        total = len(parsed.get('left_lines') or [])
        if total <= 0:
            return False
        state = json.loads(state_raw)
        return len(set(state.get('solved_lines') or [])) >= total
    except Exception:
        return False


def _state_complete_alphabetty(state_raw):
    if not state_raw:
        return False
    try:
        state = json.loads(state_raw)
    except (TypeError, ValueError):
        return False
    return bool(state.get('won'))


def is_task_completion_state(task, state_raw):
    if task is None:
        return False
    if task.task_type == 'raddle':
        return _state_complete_raddle(task, state_raw)
    if task.task_type == 'replacements_lines':
        return _state_complete_replacements(task, state_raw)
    if task.task_type == 'alphabetty':
        return _state_complete_alphabetty(state_raw)
    return False


def resolve_task_group_link(game, task_group):
    if game is None or task_group is None:
        return None
    return (
        GameTaskGroup.objects.filter(game=game, task_group=task_group)
        .only('id', 'number')
        .first()
    )


def public_game_id_for_task_group(game, task_group):
    link = resolve_task_group_link(game, task_group)
    if link and str(link.number or '').strip():
        return str(link.number)
    if task_group is None:
        return ''
    return str(task_group.pk)


def game_instance_id_for_task_group(game, task_group):
    if game is None or task_group is None:
        return ''
    return '{}:{}'.format(game.id, task_group.pk)


def _ensure_completed_record(
    *,
    team=None,
    user=None,
    anon_key=None,
    game,
    task_group,
    game_kind,
    result,
):
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return None, False
    instance_id = game_instance_id_for_task_group(game, task_group)
    public_id = public_game_id_for_task_group(game, task_group)
    defaults = {
        'game': game,
        'task_group': task_group,
        'game_kind': game_kind,
        'public_game_id': public_id,
        'result': result,
    }
    try:
        record, created = PlayerCompletedGame.objects.get_or_create(
            game_instance_id=instance_id,
            defaults=defaults,
            **actor
        )
    except IntegrityError:
        record = _completed_games_qs(team=team, user=user, anon_key=anon_key).get(
            game_instance_id=instance_id
        )
        created = False
    updated = []
    if record.game_id != game.id:
        record.game = game
        updated.append('game')
    if record.task_group_id != task_group.id:
        record.task_group = task_group
        updated.append('task_group')
    if record.game_kind != game_kind:
        record.game_kind = game_kind
        updated.append('game_kind')
    if record.public_game_id != public_id:
        record.public_game_id = public_id
        updated.append('public_game_id')
    if created and record.result != result:
        record.result = result
        updated.append('result')
    if updated:
        record.save(update_fields=updated)
    return record, created


def _backfill_supported_game_completions(*, team=None, user=None, anon_key=None):
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return
    from games.models import ChainTaskState

    qs = (
        ChainTaskState.objects.select_related('task', 'task__task_group', 'game')
        .filter(**actor)
        .filter(task__task_type__in=('raddle', 'replacements_lines', 'alphabetty'))
    )
    for row in qs.iterator():
        game_kind = supported_game_kind(row.game)
        if not game_kind:
            continue
        if not is_task_completion_state(row.task, row.state):
            continue
        _ensure_completed_record(
            team=team,
            user=user,
            anon_key=anon_key,
            game=row.game,
            task_group=row.task.task_group,
            game_kind=game_kind,
            result=PlayerCompletedGame.RESULT_SOLVED,
        )


@transaction.atomic
def register_completed_game(
    *,
    team=None,
    user=None,
    anon_key=None,
    analytics_user=None,
    task,
    game,
    result=PlayerCompletedGame.RESULT_SOLVED,
):
    game_kind = supported_game_kind(game)
    if not game_kind or task is None or task.task_group is None:
        return []

    analytics_actor = _analytics_actor_kwargs(
        analytics_user=analytics_user,
        user=user,
        anon_key=anon_key,
    )
    if analytics_actor is None:
        return []

    # Безопасно бэкфиллим только личную/анонимную историю: командные CTS/Attempt
    # не содержат автора попытки, поэтому их нельзя корректно приписывать user-level activation.
    if team is None:
        _backfill_supported_game_completions(**analytics_actor)
    state, _ = PlayerAnalyticsState.objects.get_or_create(
        defaults={},
        **analytics_actor
    )
    before_count = _completed_games_qs(**analytics_actor).count()
    if before_count >= 3 and state.activated_at is None:
        state.activated_at = timezone.now()
        state.save(update_fields=['activated_at', 'updated_at'])

    record, created = _ensure_completed_record(
        **analytics_actor,
        game=game,
        task_group=task.task_group,
        game_kind=game_kind,
        result=result,
    )
    if record is None or not created:
        return []

    goals = [
        yandex_goal_payload(
            YANDEX_GOAL_GAME_COMPLETE,
            params={
                'game': game_kind,
                'result': result,
                'game_id': record.public_game_id or record.game_instance_id,
            },
            key='{}:{}:{}'.format(
                YANDEX_GOAL_GAME_COMPLETE,
                game_kind,
                record.game_instance_id,
            ),
        )
    ]
    after_count = before_count + 1
    if before_count < 3 <= after_count and state.activated_at is None:
        state.activated_at = timezone.now()
        state.save(update_fields=['activated_at', 'updated_at'])
        goals.append(
            yandex_goal_payload(
                YANDEX_GOAL_ACTIVATED_PLAYER,
                params={'games_completed': after_count},
                key='{}:{}'.format(YANDEX_GOAL_ACTIVATED_PLAYER, after_count),
            )
        )
    return goals
