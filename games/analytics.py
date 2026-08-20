import json

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID
from games.models import (
    GameTaskGroup,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
)


YANDEX_GOAL_SIGNUP = 'signup'
YANDEX_GOAL_GAME_START = 'game_start'
YANDEX_GOAL_GAME_COMPLETE = 'game_complete'
YANDEX_GOAL_ACTIVATED_PLAYER = 'activated_player'
YANDEX_GOAL_TICKET_CHECKOUT = 'ticket_checkout'
YANDEX_GOAL_TICKET_PURCHASE = 'ticket_purchase'

SESSION_KEY_PENDING_GOALS = 'interoves_pending_yandex_goals'
ANALYTICS_ACK_SIGNING_SALT = 'games.analytics.goal-ack.v1'

GAME_KIND_BY_ID = {
    LADDER_GAME_ID: 'ladder',
    ALPHABETTY_GAME_ID: 'alphabet',
    'replacements': 'replacement',
}


def yandex_goal_payload(goal, params=None, key=None, ack=None):
    payload = {
        'goal': goal,
        'params': params or {},
        'key': key or goal,
    }
    if ack:
        payload['ack'] = ack
    return payload


def queue_pending_goal(request, goal, params=None, key=None, ack=None):
    if request is None or not hasattr(request, 'session'):
        return
    goals = list(request.session.get(SESSION_KEY_PENDING_GOALS, []) or [])
    payload = yandex_goal_payload(goal, params=params, key=key, ack=ack)
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


def analytics_game_kind(game):
    """Stable Metrika/backend value for every game, with friendly daily-game aliases."""
    if game is None:
        return None
    game_id = str(getattr(game, 'id', '') or '').strip()
    if not game_id:
        return None
    return GAME_KIND_BY_ID.get(game_id) or game_id[:100]


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


def _started_games_qs(*, team=None, user=None, anon_key=None):
    if team is not None:
        return PlayerStartedGame.objects.filter(team=team, user__isnull=True, anon_key__isnull=True)
    if user is not None:
        return PlayerStartedGame.objects.filter(user=user, team__isnull=True, anon_key__isnull=True)
    if anon_key:
        return PlayerStartedGame.objects.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    return PlayerStartedGame.objects.none()


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


def analytics_ack_payload(kind, record_id):
    token = signing.dumps(
        {'kind': kind, 'id': record_id},
        salt=ANALYTICS_ACK_SIGNING_SALT,
        compress=True,
    )
    return {
        'url': reverse('analytics_goal_ack'),
        'token': token,
    }


def _completed_goal_payload(record):
    return yandex_goal_payload(
        YANDEX_GOAL_GAME_COMPLETE,
        params={
            'game': record.game_kind,
            'result': record.result,
            'game_id': record.public_game_id or record.game_instance_id,
        },
        key='{}:{}:{}'.format(
            YANDEX_GOAL_GAME_COMPLETE,
            record.pk,
            record.game_instance_id,
        ),
        ack=analytics_ack_payload(YANDEX_GOAL_GAME_COMPLETE, record.pk),
    )


def _activation_goal_payload(state, games_completed):
    return yandex_goal_payload(
        YANDEX_GOAL_ACTIVATED_PLAYER,
        params={'games_completed': games_completed},
        key='{}:{}'.format(YANDEX_GOAL_ACTIVATED_PLAYER, state.pk),
        ack=analytics_ack_payload(YANDEX_GOAL_ACTIVATED_PLAYER, state.pk),
    )


def signup_goal_payload(state):
    if state is None or state.signup_at is None or state.signup_goal_acked_at is not None:
        return None
    return yandex_goal_payload(
        YANDEX_GOAL_SIGNUP,
        params={'method': state.signup_method or 'email'},
        key='{}:{}'.format(YANDEX_GOAL_SIGNUP, state.pk),
        ack=analytics_ack_payload(YANDEX_GOAL_SIGNUP, state.pk),
    )


def ticket_purchase_goal_payload(ticket_request):
    if (
        ticket_request is None
        or ticket_request.status != 'Accepted'
        or ticket_request.purchase_goal_sent_at is not None
    ):
        return None
    return yandex_goal_payload(
        YANDEX_GOAL_TICKET_PURCHASE,
        params={
            'amount': ticket_request.money,
            'currency': ticket_request.currency,
        },
        key='{}:{}'.format(YANDEX_GOAL_TICKET_PURCHASE, ticket_request.pk),
        ack=analytics_ack_payload(YANDEX_GOAL_TICKET_PURCHASE, ticket_request.pk),
    )


def pending_ticket_purchase_goals(user, limit=20):
    """Accepted purchases stay pending across page loads until Metrika acks them."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    from games.models import TicketRequest

    tickets = (
        TicketRequest.objects.filter(
            status='Accepted',
            purchase_goal_queued_at__isnull=False,
            purchase_goal_sent_at__isnull=True,
        )
        .filter(
            Q(created_by=user)
            | Q(
                created_by__isnull=True,
                team__member_links__profile__user=user,
            )
        )
        .order_by('purchase_goal_queued_at', 'pk')
        .distinct()[:limit]
    )
    return [payload for payload in map(ticket_purchase_goal_payload, tickets) if payload]


def pending_signup_goals(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    state = PlayerAnalyticsState.objects.filter(
        user=user,
        team__isnull=True,
        anon_key__isnull=True,
        signup_at__isnull=False,
        signup_goal_acked_at__isnull=True,
    ).first()
    payload = signup_goal_payload(state)
    return [payload] if payload else []


@transaction.atomic
def register_started_game(
    *,
    team=None,
    user=None,
    anon_key=None,
    analytics_user=None,
    task,
    game,
):
    """Persist one real start and return its goal until Metrika delivery is acked."""
    game_kind = analytics_game_kind(game)
    if not game_kind or task is None or task.task_group is None:
        return []
    actor = _analytics_actor_kwargs(
        analytics_user=analytics_user,
        user=user,
        anon_key=anon_key,
    )
    if actor is None:
        actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return []

    instance_id = game_instance_id_for_task_group(game, task.task_group)
    public_id = public_game_id_for_task_group(game, task.task_group)
    defaults = {
        'game': game,
        'task_group': task.task_group,
        'game_kind': game_kind,
        'public_game_id': public_id,
    }
    try:
        with transaction.atomic():
            record, _created = PlayerStartedGame.objects.get_or_create(
                game_instance_id=instance_id,
                defaults=defaults,
                **actor
            )
    except IntegrityError:
        record = _started_games_qs(**actor).get(game_instance_id=instance_id)

    updated = []
    if record.game_id != game.id:
        record.game = game
        updated.append('game')
    if record.task_group_id != task.task_group_id:
        record.task_group = task.task_group
        updated.append('task_group')
    if record.game_kind != game_kind:
        record.game_kind = game_kind
        updated.append('game_kind')
    if record.public_game_id != public_id:
        record.public_game_id = public_id
        updated.append('public_game_id')
    if updated:
        record.save(update_fields=updated)

    if record.metrika_acked_at is not None or record.is_backfilled:
        return []
    return [
        yandex_goal_payload(
            YANDEX_GOAL_GAME_START,
            params={
                'game': game_kind,
                'game_id': record.public_game_id or record.game_instance_id,
            },
            key='{}:{}:{}'.format(
                YANDEX_GOAL_GAME_START,
                record.pk,
                record.game_instance_id,
            ),
            ack=analytics_ack_payload(YANDEX_GOAL_GAME_START, record.pk),
        )
    ]


def acknowledge_analytics_goal(token):
    try:
        payload = signing.loads(
            token,
            salt=ANALYTICS_ACK_SIGNING_SALT,
            max_age=60 * 60 * 24 * 14,
        )
    except signing.BadSignature:
        return False
    if not isinstance(payload, dict):
        return False
    kind = payload.get('kind')
    record_id = payload.get('id')
    if kind == YANDEX_GOAL_GAME_START:
        updated = PlayerStartedGame.objects.filter(
            pk=record_id,
            metrika_acked_at__isnull=True,
        ).update(metrika_acked_at=timezone.now())
        return bool(updated or PlayerStartedGame.objects.filter(pk=record_id).exists())
    if kind == YANDEX_GOAL_GAME_COMPLETE:
        updated = PlayerCompletedGame.objects.filter(
            pk=record_id,
            metrika_acked_at__isnull=True,
        ).update(metrika_acked_at=timezone.now())
        return bool(updated or PlayerCompletedGame.objects.filter(pk=record_id).exists())
    if kind in (YANDEX_GOAL_SIGNUP, YANDEX_GOAL_ACTIVATED_PLAYER):
        field = (
            'signup_goal_acked_at'
            if kind == YANDEX_GOAL_SIGNUP
            else 'activation_goal_acked_at'
        )
        state = PlayerAnalyticsState.objects.filter(pk=record_id)
        updated = state.filter(**{'{}__isnull'.format(field): True}).update(
            **{field: timezone.now()}
        )
        return bool(updated or state.exists())
    if kind in (YANDEX_GOAL_TICKET_CHECKOUT, YANDEX_GOAL_TICKET_PURCHASE):
        from games.models import TicketRequest

        tickets = TicketRequest.objects.filter(pk=record_id)
        if kind == YANDEX_GOAL_TICKET_PURCHASE:
            tickets = tickets.filter(status='Accepted')
            field = 'purchase_goal_sent_at'
        else:
            field = 'checkout_goal_acked_at'
        updated = tickets.filter(**{'{}__isnull'.format(field): True}).update(
            **{field: timezone.now()}
        )
        return bool(updated or tickets.exists())
    return False


def _ensure_completed_record(
    *,
    team=None,
    user=None,
    anon_key=None,
    game,
    task_group,
    game_kind,
    result,
    is_backfilled=False,
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
        'is_backfilled': is_backfilled,
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


def _backfill_supported_game_completions(
    *, team=None, user=None, anon_key=None, exclude_instance_id=None
):
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
        if game_instance_id_for_task_group(row.game, row.task.task_group) == exclude_instance_id:
            continue
        _ensure_completed_record(
            team=team,
            user=user,
            anon_key=anon_key,
            game=row.game,
            task_group=row.task.task_group,
            game_kind=game_kind,
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=True,
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

    current_instance_id = game_instance_id_for_task_group(game, task.task_group)

    # Безопасно бэкфиллим только личную/анонимную историю: командные CTS/Attempt
    # не содержат автора попытки, поэтому их нельзя корректно приписывать user-level activation.
    if team is None:
        _backfill_supported_game_completions(
            **analytics_actor,
            exclude_instance_id=current_instance_id,
        )
    state, _ = PlayerAnalyticsState.objects.get_or_create(
        defaults={},
        **analytics_actor
    )
    before_count = _completed_games_qs(**analytics_actor).count()
    if before_count >= 3 and state.activated_at is None:
        state.activated_at = timezone.now()
        state.activation_is_backfilled = True
        state.save(update_fields=['activated_at', 'activation_is_backfilled', 'updated_at'])

    record, created = _ensure_completed_record(
        **analytics_actor,
        game=game,
        task_group=task.task_group,
        game_kind=game_kind,
        result=result,
        is_backfilled=False,
    )
    if record is None:
        return []

    goals = []
    if not record.is_backfilled and record.metrika_acked_at is None:
        goals.append(_completed_goal_payload(record))

    after_count = _completed_games_qs(**analytics_actor).count()
    if before_count < 3 <= after_count and state.activated_at is None:
        state.activated_at = timezone.now()
        state.save(update_fields=['activated_at', 'updated_at'])
    if (
        state.activated_at is not None
        and not state.activation_is_backfilled
        and state.activation_goal_acked_at is None
    ):
        goals.append(_activation_goal_payload(state, max(3, after_count)))
    return goals
