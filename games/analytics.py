import hashlib
import json
import logging
import time
from contextlib import contextmanager
from functools import wraps

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID
from games.word_salad import WORD_SALAD_GAME_ID
from games.models import (
    Attempt,
    ChainTaskState,
    GameTaskGroup,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
)
from games.analytics_persistence import (
    AnalyticsRowInvariantError,
    create_or_reread_analytics_row,
)


logger = logging.getLogger(__name__)
analytics_timing_logger = logging.getLogger('interoves.analytics_timing')


@contextmanager
def _analytics_timed_phase(phases, name):
    """Accumulate elapsed time for a completion-analytics phase, when enabled."""
    if phases is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        phases[name] = phases.get(name, 0.0) + (time.perf_counter() - started) * 1000.0


def _measure_completed_game_timing(func):
    """Log privacy-safe phase timings for the synchronous completion path."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        phases = {}
        backfill_counts = {
            'chain_states_scanned': 0,
            'completion_candidates': 0,
            'existing_records': 0,
            'created_records': 0,
        }
        kwargs['_timing_phases'] = phases
        kwargs['_backfill_counts'] = backfill_counts
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            total_ms = (time.perf_counter() - started) * 1000.0
            phases['other_ms'] = max(0.0, total_ms - sum(phases.values()))
            analytics_timing_logger.info(
                'analytics_completed_timing total_ms=%.1f '
                'history_backfill_ms=%.1f chain_states_scanned=%d '
                'completion_candidates=%d existing_records=%d created_records=%d '
                'analytics_state_get_or_create_ms=%.1f completed_count_before_ms=%.1f '
                'completion_group_check_ms=%.1f current_completion_record_ms=%.1f '
                'daily_statistics_invalidation_ms=%.1f completed_count_after_ms=%.1f '
                'activation_state_ms=%.1f other_ms=%.1f',
                total_ms,
                phases.get('history_backfill_ms', 0.0),
                backfill_counts['chain_states_scanned'],
                backfill_counts['completion_candidates'],
                backfill_counts['existing_records'],
                backfill_counts['created_records'],
                phases.get('analytics_state_get_or_create_ms', 0.0),
                phases.get('completed_count_before_ms', 0.0),
                phases.get('completion_group_check_ms', 0.0),
                phases.get('current_completion_record_ms', 0.0),
                phases.get('daily_statistics_invalidation_ms', 0.0),
                phases.get('completed_count_after_ms', 0.0),
                phases.get('activation_state_ms', 0.0),
                phases['other_ms'],
            )
    return wrapped


YANDEX_GOAL_SIGNUP = 'signup'
YANDEX_GOAL_GAME_START = 'game_start'
YANDEX_GOAL_GAME_COMPLETE = 'game_complete'
YANDEX_GOAL_ACTIVATED_PLAYER = 'activated_player'
YANDEX_GOAL_TICKET_CHECKOUT = 'ticket_checkout'
YANDEX_GOAL_TICKET_PURCHASE = 'ticket_purchase'
YANDEX_GOAL_SUBSCRIPTION_VIEW = 'subscription_view'
YANDEX_GOAL_SUBSCRIPTION_CHECKOUT = 'subscription_checkout_start'
YANDEX_GOAL_SUBSCRIPTION_PAYMENT = 'subscription_payment_success'
YANDEX_GOAL_SUBSCRIPTION_RENEWAL = 'subscription_renewal_success'
YANDEX_GOAL_SUBSCRIPTION_CANCELLED = 'subscription_cancelled'
YANDEX_GOAL_NEXT_GAME_VOTE_VIEW = 'next_game_vote_view'
YANDEX_GOAL_NEXT_GAME_VOTE_CLICK = 'next_game_vote_click'
YANDEX_GOAL_NEXT_GAME_VOTE_RETURN = 'next_game_vote_tribute_return'
YANDEX_GOAL_NEXT_GAME_VOTE_PAYMENT = 'next_game_vote_payment'

SESSION_KEY_PENDING_GOALS = 'interoves_pending_yandex_goals'
ANALYTICS_ACK_SIGNING_SALT = 'games.analytics.goal-ack.v1'
PRODUCT_ANALYTICS_INSTRUMENTATION_VERSION = 2

GAME_KIND_BY_ID = {
    LADDER_GAME_ID: 'ladder',
    ALPHABETTY_GAME_ID: 'alphabet',
    WORD_SALAD_GAME_ID: 'salad',
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


def _swallow_analytics_invariant(fn):
    """Keep gameplay JSON endpoints alive if analytics rows are inconsistent."""

    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AnalyticsRowInvariantError:
            logger.exception('%s analytics invariant', fn.__name__)
            return []

    return wrapped


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


def _state_complete_word_salad(task, state_raw):
    if not state_raw:
        return False
    try:
        from games.word_salad import load_state, parse_task_data

        _grid, words = parse_task_data(task.checker_data, task.answer)
        solved = set(load_state(state_raw).get('solved_indices') or [])
        return bool(words) and set(range(len(words))).issubset(solved)
    except Exception:
        return False


def is_task_completion_state(task, state_raw):
    if task is None:
        return False
    if task.task_type == 'raddle':
        return _state_complete_raddle(task, state_raw)
    if task.task_type == 'replacements_lines':
        return _state_complete_replacements(task, state_raw)
    if task.task_type == 'alphabetty':
        return _state_complete_alphabetty(state_raw)
    if task.task_type == 'word_salad':
        return _state_complete_word_salad(task, state_raw)
    return False


def is_task_group_complete(*, task_group, game, team=None, user=None, anon_key=None,
                           mode='general', replay_slot=None):
    """Return whether every visible task in this game group is solved by actor."""
    tasks = list(task_group.tasks.visible())
    if not tasks:
        return False

    chain_types = {'raddle', 'replacements_lines', 'alphabetty', 'word_salad'}
    chain_mode = 'tournament' if mode == 'tournament' else 'general'
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    for task in tasks:
        if task.task_type in chain_types:
            state = ChainTaskState.objects.filter(
                task=task,
                game=game,
                replay_slot=replay_slot,
                game_mode=chain_mode,
                **actor,
            ).values_list('state', flat=True).first()
            if not is_task_completion_state(task, state):
                return False
            continue

        attempts = Attempt.manager.get_attempts_info(
            team=team,
            task=task,
            mode=mode,
            user=user,
            anon_key=anon_key,
            game=game,
            replay_slot=replay_slot,
        )
        if not attempts.is_solved():
            return False
    return True


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


def _subscription_goal_payload(subscription, kind):
    if subscription is None:
        return None
    from games.models import ClubSubscription

    params = {
        'provider': subscription.provider,
        'currency': (subscription.currency or '').lower(),
        'amount': subscription.amount,
    }
    if kind == YANDEX_GOAL_SUBSCRIPTION_PAYMENT:
        if subscription.payment_success_goal_sent_at is not None:
            return None
        if subscription.payment_success_goal_queued_at is None:
            return None
        field_id = 'payment'
    elif kind == YANDEX_GOAL_SUBSCRIPTION_RENEWAL:
        if subscription.renewal_goal_sent_at is not None:
            return None
        if subscription.renewal_goal_queued_at is None:
            return None
        field_id = 'renewal'
    elif kind == YANDEX_GOAL_SUBSCRIPTION_CANCELLED:
        if subscription.cancelled_goal_sent_at is not None:
            return None
        if subscription.cancelled_goal_queued_at is None:
            return None
        field_id = 'cancelled'
    else:
        return None
    return yandex_goal_payload(
        kind,
        params=params,
        key='{}:{}:{}'.format(kind, subscription.pk, field_id),
        ack=analytics_ack_payload(kind, subscription.pk),
    )


def next_game_vote_payment_goal_payload(event):
    if event is None or event.analytics_goal_queued_at is None or event.analytics_goal_sent_at is not None:
        return None
    if not event.candidate or not event.include_in_scoreboard:
        return None
    from decimal import Decimal

    amount = (Decimal(event.normalized_amount_eur_cents) / Decimal(100)).quantize(Decimal('0.01'))
    return yandex_goal_payload(
        YANDEX_GOAL_NEXT_GAME_VOTE_PAYMENT,
        params={
            'candidate': event.candidate,
            'currency': (event.original_currency or '').lower(),
            'normalized_amount_eur': str(amount),
        },
        key='{}:{}'.format(YANDEX_GOAL_NEXT_GAME_VOTE_PAYMENT, event.pk),
        ack=analytics_ack_payload(YANDEX_GOAL_NEXT_GAME_VOTE_PAYMENT, event.pk),
    )


def pending_next_game_vote_payment_goals(user, limit=20):
    """Fire payment goals only for the matched Inter Oves account, never with Telegram IDs."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    from games.models import NextGameVoteEvent

    events = (
        NextGameVoteEvent.objects.filter(
            matched_user=user,
            include_in_scoreboard=True,
            excluded=False,
            analytics_goal_queued_at__isnull=False,
            analytics_goal_sent_at__isnull=True,
        )
        .order_by('analytics_goal_queued_at', 'pk')[:limit]
    )
    return [payload for payload in map(next_game_vote_payment_goal_payload, events) if payload]


def pending_subscription_goals(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    from games.models import ClubSubscription

    subscription = ClubSubscription.objects.filter(user=user).first()
    if subscription is None:
        return []
    payloads = [
        _subscription_goal_payload(subscription, YANDEX_GOAL_SUBSCRIPTION_PAYMENT),
        _subscription_goal_payload(subscription, YANDEX_GOAL_SUBSCRIPTION_RENEWAL),
        _subscription_goal_payload(subscription, YANDEX_GOAL_SUBSCRIPTION_CANCELLED),
    ]
    return [payload for payload in payloads if payload]


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
@_swallow_analytics_invariant
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
        'instrumentation_version': PRODUCT_ANALYTICS_INSTRUMENTATION_VERSION,
    }
    lookup = dict(actor, game_instance_id=instance_id)
    record, _created = create_or_reread_analytics_row(
        PlayerStartedGame,
        lookup=lookup,
        defaults=defaults,
    )

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
    if kind in (
        YANDEX_GOAL_SUBSCRIPTION_PAYMENT,
        YANDEX_GOAL_SUBSCRIPTION_RENEWAL,
        YANDEX_GOAL_SUBSCRIPTION_CANCELLED,
    ):
        from games.models import ClubSubscription

        field = {
            YANDEX_GOAL_SUBSCRIPTION_PAYMENT: 'payment_success_goal_sent_at',
            YANDEX_GOAL_SUBSCRIPTION_RENEWAL: 'renewal_goal_sent_at',
            YANDEX_GOAL_SUBSCRIPTION_CANCELLED: 'cancelled_goal_sent_at',
        }[kind]
        rows = ClubSubscription.objects.filter(pk=record_id)
        updated = rows.filter(**{'{}__isnull'.format(field): True}).update(
            **{field: timezone.now()}
        )
        return bool(updated or rows.exists())
    if kind == YANDEX_GOAL_NEXT_GAME_VOTE_PAYMENT:
        from games.models import NextGameVoteEvent

        rows = NextGameVoteEvent.objects.filter(pk=record_id)
        updated = rows.filter(analytics_goal_sent_at__isnull=True).update(
            analytics_goal_sent_at=timezone.now(),
        )
        return bool(updated or rows.exists())
    return False


def _ensure_completed_record(
    *,
    team=None,
    user=None,
    anon_key=None,
    game,
    task,
    game_kind,
    result,
    is_backfilled=False,
    source='unknown',
    mode='general',
    completion_team=None,
    completion_user=None,
    completion_anon_key=None,
    _timing_phases=None,
    _group_is_complete=None,
    _suppress_creation_log=False,
):
    """Persist a completion only after the canonical group check passes.

    Callers may supply a separate gameplay actor for the completion check when
    analytics attribution intentionally uses another identity (for example a
    logged-in user playing for a team).
    """
    task_group = getattr(task, 'task_group', None)
    check_team = team if completion_team is None else completion_team
    check_user = user if completion_user is None else completion_user
    check_anon_key = anon_key if completion_anon_key is None else completion_anon_key
    if task is None or task_group is None:
        return None, False
    if _group_is_complete is None:
        with _analytics_timed_phase(_timing_phases, 'completion_group_check_ms'):
            group_is_complete = is_task_group_complete(
                task_group=task_group,
                game=game,
                team=check_team,
                user=check_user,
                anon_key=check_anon_key,
                mode=mode,
                replay_slot=None,
            )
    else:
        group_is_complete = _group_is_complete
    if not group_is_complete:
        return None, False
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return None, False
    with _analytics_timed_phase(_timing_phases, 'current_completion_record_ms'):
        instance_id = game_instance_id_for_task_group(game, task_group)
        public_id = public_game_id_for_task_group(game, task_group)
        defaults = {
            'game': game,
            'task_group': task_group,
            'game_kind': game_kind,
            'public_game_id': public_id,
            'result': result,
            'is_backfilled': is_backfilled,
            'instrumentation_version': (
                None if is_backfilled else PRODUCT_ANALYTICS_INSTRUMENTATION_VERSION
            ),
        }
        lookup = dict(actor, game_instance_id=instance_id)
        record, created = create_or_reread_analytics_row(
            PlayerCompletedGame,
            lookup=lookup,
            defaults=defaults,
        )
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
        if created and not _suppress_creation_log:
            if team is not None:
                actor_type, actor_id = 'team', str(team.pk)
            elif user is not None:
                actor_type, actor_id = 'user', str(user.pk)
            elif anon_key:
                actor_type, actor_id = 'anon', hashlib.sha256(str(anon_key).encode()).hexdigest()[:16]
            else:
                actor_type, actor_id = 'unknown', 'unavailable'
            required_count = task_group.tasks.visible().count()
            logger.info(
                'player_completed_game_created',
                extra={
                    'event': 'player_completed_game_created',
                    'actor_type': actor_type,
                    'actor_id': actor_id,
                    'game': game.id,
                    'game_instance_id': instance_id,
                    'task_group_id': task_group.id,
                    'completed_required_count': required_count,
                    'total_required_count': required_count,
                    'source': source,
                },
            )
    return record, created


def reconcile_completed_game_after_recheck(
    *, team=None, user=None, anon_key=None, task, game, mode='general',
):
    """Reconcile the official completion flag with the rebuilt game state.

    Rechecks can both add and remove the final solved step.  Keep the
    PlayerCompletedGame row in sync without emitting a new analytics goal for
    a historical/admin repair.
    """
    game_kind = supported_game_kind(game)
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    task_group = getattr(task, 'task_group', None)
    if not game_kind or actor is None or task_group is None:
        return False

    instance_id = game_instance_id_for_task_group(game, task_group)
    completion_qs = PlayerCompletedGame.objects.filter(
        game_instance_id=instance_id,
        **actor,
    )
    if is_task_group_complete(
        task_group=task_group,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=None,
    ):
        record, _created = _ensure_completed_record(
            team=team,
            user=user,
            anon_key=anon_key,
            game=game,
            task=task,
            game_kind=game_kind,
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=True,
            source='task_recheck',
            mode=mode,
        )
        if record is not None and record.result != PlayerCompletedGame.RESULT_SOLVED:
            completion_qs.update(result=PlayerCompletedGame.RESULT_SOLVED)
        changed = True
    else:
        changed = bool(completion_qs.delete()[0])

    from games.daily_statistics import invalidate_daily_statistics
    invalidate_daily_statistics(game.id, task_group.id)
    return changed


def _backfill_supported_game_completions(
    *, team=None, user=None, anon_key=None, exclude_instance_id=None,
    _backfill_counts=None,
):
    """Backfill analytics rows from completed chain tasks, never from one task alone.

    A completed chain state is only a candidate.  The mandatory group check in
    ``_ensure_completed_record`` decides whether a row may be created.
    """
    actor = _actor_kwargs(team=team, user=user, anon_key=anon_key)
    if actor is None:
        return _backfill_counts or {
            'chain_states_scanned': 0,
            'completion_candidates': 0,
            'existing_records': 0,
            'created_records': 0,
        }
    counts = _backfill_counts if _backfill_counts is not None else {
        'chain_states_scanned': 0,
        'completion_candidates': 0,
        'existing_records': 0,
        'created_records': 0,
    }

    candidates = _legacy_completion_candidates(
        actor=actor,
        exclude_instance_id=exclude_instance_id,
        counts=counts,
    )

    counts['completion_candidates'] = len(candidates)
    for row, game_kind in candidates.values():
        record, created = _ensure_completed_record(
            team=team,
            user=user,
            anon_key=anon_key,
            game=row.game,
            task=row.task,
            game_kind=game_kind,
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=True,
            source='analytics_backfill',
            mode=row.game_mode,
        )
        if record is not None:
            if created:
                counts['created_records'] += 1
            else:
                counts['existing_records'] += 1
    return counts


def _legacy_completion_candidates(
    *, actor, exclude_instance_id=None, counts=None, updated_before=None,
):
    """Return the exact candidate set used by the legacy backfill.

    This is shared by the hot-path backfill, the one-time reconciliation command,
    and its read-only audit mode so completion semantics cannot drift between
    them.
    """
    from games.models import ChainTaskState

    counts = counts if counts is not None else {}
    qs = (
        ChainTaskState.objects.select_related('task', 'task__task_group', 'game')
        .filter(**actor)
        .filter(replay_slot__isnull=True)
        .filter(task__task_type__in=(
            'raddle', 'replacements_lines', 'alphabetty', 'word_salad',
        ))
    )
    if updated_before is not None:
        qs = qs.filter(updated_at__lte=updated_before)
    candidates = {}
    for row in qs.iterator():
        counts['chain_states_scanned'] = counts.get('chain_states_scanned', 0) + 1
        game_kind = supported_game_kind(row.game)
        if not game_kind:
            continue
        if not is_task_completion_state(row.task, row.state):
            continue
        if game_instance_id_for_task_group(row.game, row.task.task_group) == exclude_instance_id:
            continue
        candidates.setdefault(
            (row.game_id, row.task.task_group_id, row.game_mode),
            (row, game_kind),
        )
    counts['completion_candidates'] = len(candidates)
    return candidates


def reconcile_legacy_completed_games_for_actor(
    *, user=None, anon_key=None, dry_run=False, activation_cutoff=None,
    history_cutoff=None,
):
    """Reconcile one personal actor using the production backfill contract.

    ``dry_run`` performs the same candidate and group checks without writes.
    ``activation_cutoff`` prevents concurrent live completions from being
    classified as historical activation evidence during the one-time batch.
    """
    actor = _actor_kwargs(user=user, anon_key=anon_key)
    if actor is None:
        return {
            'chain_states_scanned': 0,
            'completion_candidates': 0,
            'legacy_complete_instances': 0,
            'incomplete_candidates': 0,
            'existing_records': 0,
            'created_records': 0,
            'missing_records': 0,
            'ambiguous_records': 0,
        }

    counts = {
        'chain_states_scanned': 0,
        'completion_candidates': 0,
        'legacy_complete_instances': 0,
        'incomplete_candidates': 0,
        'existing_records': 0,
        'created_records': 0,
        'missing_records': 0,
        'ambiguous_records': 0,
    }
    candidates = _legacy_completion_candidates(
        actor=actor,
        counts=counts,
        updated_before=history_cutoff,
    )
    for row, game_kind in candidates.values():
        complete = is_task_group_complete(
            task_group=row.task.task_group,
            game=row.game,
            user=user,
            anon_key=anon_key,
            mode=row.game_mode,
            replay_slot=None,
        )
        if not complete:
            counts['incomplete_candidates'] += 1
            continue
        counts['legacy_complete_instances'] += 1

        instance_id = game_instance_id_for_task_group(row.game, row.task.task_group)
        if dry_run:
            existing = list(
                PlayerCompletedGame.objects.filter(
                    **actor,
                    game_instance_id=instance_id,
                ).values_list('pk', flat=True)[:2]
            )
            if len(existing) > 1:
                counts['ambiguous_records'] += 1
                continue
            if existing:
                counts['existing_records'] += 1
            else:
                counts['missing_records'] += 1
            continue

        record, created = _ensure_completed_record(
            user=user,
            anon_key=anon_key,
            game=row.game,
            task=row.task,
            game_kind=game_kind,
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=True,
            source='legacy_reconciliation_batch',
            mode=row.game_mode,
            _group_is_complete=True,
            _suppress_creation_log=True,
        )
        if record is None:
            counts['incomplete_candidates'] += 1
        elif created:
            counts['created_records'] += 1
        else:
            counts['existing_records'] += 1

    if dry_run:
        return counts

    historical_count_qs = _completed_games_qs(**actor)
    if activation_cutoff is None:
        historical_count = historical_count_qs.count()
    else:
        historical_count = historical_count_qs.filter(
            Q(is_backfilled=True) | Q(completed_at__lte=activation_cutoff),
        ).count()
    if historical_count < 3:
        return counts
    state, _ = create_or_reread_analytics_row(
        PlayerAnalyticsState,
        lookup=actor,
    )
    if historical_count >= 3 and state.activated_at is None:
        activated_at = timezone.now()
        PlayerAnalyticsState.objects.filter(
            pk=state.pk,
            activated_at__isnull=True,
        ).update(
            activated_at=activated_at,
            activation_is_backfilled=True,
            updated_at=activated_at,
        )
    return counts


@_swallow_analytics_invariant
@_measure_completed_game_timing
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
    mode='general',
    _timing_phases=None,
    _backfill_counts=None,
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

    with _analytics_timed_phase(_timing_phases, 'analytics_state_get_or_create_ms'):
        state, _ = create_or_reread_analytics_row(
            PlayerAnalyticsState,
            lookup=analytics_actor,
        )
    with _analytics_timed_phase(_timing_phases, 'completed_count_before_ms'):
        before_count = _completed_games_qs(**analytics_actor).count()
    with _analytics_timed_phase(_timing_phases, 'activation_state_ms'):
        if before_count >= 3 and state.activated_at is None:
            activated_at = timezone.now()
            PlayerAnalyticsState.objects.filter(
                pk=state.pk,
                activated_at__isnull=True,
            ).update(
                activated_at=activated_at,
                activation_is_backfilled=True,
                updated_at=activated_at,
            )
            state.refresh_from_db()

    record, created = _ensure_completed_record(
        **analytics_actor,
        game=game,
        task=task,
        game_kind=game_kind,
        result=result,
        is_backfilled=False,
        source='task_completion',
        mode=mode,
        completion_team=team,
        completion_user=user,
        completion_anon_key=anon_key,
        _timing_phases=_timing_phases,
    )
    if record is None:
        return []

    # The gameplay path has saved the Attempt/state before reaching this point,
    # so the completion check reads the canonical persisted representation.
    from games.daily_statistics import invalidate_daily_statistics
    with _analytics_timed_phase(_timing_phases, 'daily_statistics_invalidation_ms'):
        invalidate_daily_statistics(game.id, task.task_group_id)

    goals = []
    if not record.is_backfilled and record.metrika_acked_at is None:
        goals.append(_completed_goal_payload(record))

    with _analytics_timed_phase(_timing_phases, 'completed_count_after_ms'):
        after_count = _completed_games_qs(**analytics_actor).count()
    with _analytics_timed_phase(_timing_phases, 'activation_state_ms'):
        if before_count < 3 <= after_count and state.activated_at is None:
            activated_at = timezone.now()
            PlayerAnalyticsState.objects.filter(
                pk=state.pk,
                activated_at__isnull=True,
            ).update(
                activated_at=activated_at,
                activation_is_backfilled=False,
                updated_at=activated_at,
            )
        # A concurrent completion/signup may have won either conditional update.
        # Build any goal only from the canonical database state, never this stale
        # Python instance.
        state.refresh_from_db()
        if (
            state.activated_at is not None
            and not state.activation_is_backfilled
            and state.activation_goal_acked_at is None
        ):
            goals.append(_activation_goal_payload(state, max(3, after_count)))
    return goals
