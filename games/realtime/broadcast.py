"""Commit-time broadcast and task-change events."""
import logging
import queue
import threading

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from games.site.http.auth_observability import log_realtime_sync
from games.models import Attempt, GameTaskGroup
from games.realtime.counter import envelope_track_message, msgpack_safe_keys, next_track_seq
from games.realtime.groups import (
    CHANNEL_GROUPS,
    game_track_namespace,
    team_track_namespace,
    user_game_track_namespace,
    user_track_namespace,
)
from games.results.share import DEFAULT_SHARE_HOST
from games.site.views.game.render import update_task_html

logger = logging.getLogger('games.views.track')

_CHANNEL_BROADCAST_QUEUE_MAXSIZE = 64
_CHANNEL_BROADCAST_WORKER_COUNT = 2
_channel_broadcast_queue = queue.Queue(maxsize=_CHANNEL_BROADCAST_QUEUE_MAXSIZE)
_channel_broadcast_workers_lock = threading.Lock()
_channel_broadcast_workers_started = False


def _channel_broadcast_worker():
    while True:
        job = _channel_broadcast_queue.get()
        try:
            job()
        except Exception:
            logger.exception('Channel broadcast worker failed')
        finally:
            _channel_broadcast_queue.task_done()


def _enqueue_channel_broadcast(job):
    """Run broadcasts on a fixed worker pool; drop with a revision gap on overload."""
    global _channel_broadcast_workers_started
    if not _channel_broadcast_workers_started:
        with _channel_broadcast_workers_lock:
            if not _channel_broadcast_workers_started:
                for index in range(_CHANNEL_BROADCAST_WORKER_COUNT):
                    threading.Thread(
                        target=_channel_broadcast_worker,
                        daemon=True,
                        name='interoves_channel_broadcast_{}'.format(index + 1),
                    ).start()
                _channel_broadcast_workers_started = True
    try:
        _channel_broadcast_queue.put_nowait(job)
    except queue.Full:
        logger.error(
            'Channel broadcast queue full; dropping notification queue_size=%s',
            _channel_broadcast_queue.qsize(),
        )
        return False
    return True

def _schedule_channel_broadcast(fn, *, prepare=None):
    """
    Run fn after DB commit. Sequence allocation stays in commit-callback order;
    channel-layer I/O runs on a bounded daemon worker pool so Redis waits do not
    create one unbounded thread per mutation or block the request path.
    """

    def after_commit():
        # Allocate ordering metadata synchronously in commit-callback order.
        # Revision allocation remains synchronous to preserve commit ordering;
        # channel-layer I/O is deferred. A slower worker cannot make an older
        # event receive a newer sequence and overwrite fresher HTML.
        try:
            prepared = prepare() if prepare is not None else None
        except Exception:
            logger.exception('Channel broadcast preparation failed')
            return

        def run_safe():
            try:
                fn(prepared)
            except Exception:
                logger.exception('Channel broadcast failed')

        if getattr(settings, 'DEFER_CHANNEL_BROADCAST', True):
            _enqueue_channel_broadcast(run_safe)
        else:
            run_safe()

    transaction.on_commit(after_commit)

def _broadcast_game_track_event_commit(game_id: str, event_name: str, payload: dict):
    """Notify all sockets on track.game.{game_id} (e.g. observers on game page)."""

    namespace = game_track_namespace(game_id)

    def prepare():
        return next_track_seq(namespace)

    def send(seq):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        body = envelope_track_message(
            {
                'type': 'track.event',
                'event': event_name,
                'payload': payload,
                'seq': seq,
                'seq_namespace': namespace,
            },
            game_id,
        )
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['game'](game_id),
            body,
        )

    _schedule_channel_broadcast(send, prepare=prepare)


def notify_registered_users_game_lifecycle_changed(old_game, new_game):
    """
    When wall-clock phase changes (after Game.save), notify registered teams and
    broadcast to the game group (start/end of window per games.access).

    Uses the same moment as admin UI: game_has_started / game_has_ended at now.
    """
    from games.club.access.game import game_has_ended, game_has_started
    from games.models import Registration

    started_before = game_has_started(old_game)
    started_after = game_has_started(new_game)
    ended_before = game_has_ended(old_game)
    ended_after = game_has_ended(new_game)

    events = []
    if not started_before and started_after:
        events.append('game.started')
    if not ended_before and ended_after:
        events.append('game.ended')
    if not events:
        return

    for event_name in events:
        payload = {'game_id': new_game.id}
        _broadcast_game_track_event_commit(new_game.id, event_name, payload)

        notified = set()
        for reg in Registration.objects.filter(game=new_game).select_related('team'):
            team = reg.team
            if team is None:
                continue
            for profile in team.roster_profiles:
                uid = profile.user_id
                if uid in notified:
                    continue
                notified.add(uid)
                notify_user_after_commit(
                    uid,
                    {
                        'type': 'track.event',
                        'event': event_name,
                        'payload': dict(payload),
                    },
                )


def notify_registered_users_play_access_changed(old_game, new_game):
    """
    When a registered team's access to 'play' becomes True, notify each team member
    (e.g. is_ready flipped, times updated). Uses notify_user_after_commit.
    """
    from games.models import Registration

    notified = set()
    for reg in Registration.objects.filter(game=new_game).select_related('team'):
        team = reg.team
        if team is None:
            continue
        if old_game.has_access('play', team=team) or not new_game.has_access('play', team=team):
            continue
        for profile in team.roster_profiles:
            uid = profile.user_id
            if uid in notified:
                continue
            notified.add(uid)
            notify_user_after_commit(
                uid,
                {
                    'type': 'track.event',
                    'event': 'game.play_available',
                    'payload': {'game_id': new_game.id},
                },
            )


def notify_user_after_commit(user_id, body, *, seq_namespace=None):
    """
    Push a message to one user's track socket (TrackGame and/or UserTrackConsumer connected).
    body must include 'type' for the consumer (e.g. type='track.event' -> track_event handler).
    """
    payload = msgpack_safe_keys(dict(body))
    if seq_namespace is None:
        seq_namespace = user_track_namespace(user_id)
    payload.setdefault('seq_namespace', seq_namespace)

    def prepare():
        if 'seq' in payload:
            return payload['seq']
        return next_track_seq(seq_namespace)

    def send(seq):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        prepared_payload = dict(payload)
        prepared_payload['seq'] = seq
        async_to_sync(channel_layer.group_send)(
            CHANNEL_GROUPS['user'](user_id),
            prepared_payload,
        )

    _schedule_channel_broadcast(send, prepare=prepare)


def live_page_actor(user, session, game):
    """Actor whose progress the open game page is showing.

    Admin broadcasts have no actor of their own. Rebuilding the card from
    ``profile.team_on`` drops personal progress: a sections player who also
    belongs to a team then sees an empty salad.
    """
    team, actor_user, _mode = live_page_actor_for_mode(user, session, game)
    return team, actor_user


def live_page_actor_for_mode(user, session, game, requested_mode=None):
    """Resolve the actor and mode represented by one page/socket."""
    from games.site.ui import _get_play_mode
    from games.identity.profile import has_profile, has_team
    from games.site.session.play_mode import effective_play_mode

    class _SessionRequest:
        pass

    request = _SessionRequest()
    request.session = session if session is not None else {}
    request.user = user
    play_mode = requested_mode
    if play_mode not in ('team', 'personal'):
        play_mode, _key = _get_play_mode(request, getattr(game, 'project_id', None))
    play_mode = effective_play_mode(play_mode, game, user=user)
    if play_mode == 'team' and has_team(user):
        return user.profile.team_on, None, play_mode
    if getattr(user, 'is_authenticated', False) and has_profile(user):
        return None, user, play_mode
    return None, None, play_mode


def build_event_task_change(
    task,
    team=None,
    current_mode=None,
    update_html=None,
    request=None,
    game=None,
    user=None,
    anon_key=None,
    reason=None,
):
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        by = 'team' if team is not None else ('personal' if user is not None or anon_key else 'admin')
        return {'type': 'task.changed', 'task': task.id, 'by': by}
    if current_mode is None and (team is not None or user is not None or anon_key):
        attempt = Attempt(
            task=task,
            team=team,
            user=user,
            anon_key=anon_key,
            time=timezone.now(),
        )
        current_mode = game.get_current_mode(attempt)

    if request is None and (team is not None or user is not None):
        from django.test.client import RequestFactory
        request_user = user
        if request_user is None:
            profile = team.roster_profiles.first()
            request_user = profile.user if profile is not None else None
        if request_user is not None:
            # RequestFactory's default host is "testserver", which production
            # ALLOWED_HOSTS rejects. Salad/raddle share text calls get_host
            # while the template argument is resolved, so the live card never
            # renders and the recheck push is dropped.
            request = RequestFactory().get(
                f'/games/{game.id}',
                HTTP_HOST=DEFAULT_SHARE_HOST,
            )
            request.user = request_user

    if update_html is None and request is not None:
        update_html = update_task_html(
            request,
            task,
            team,
            current_mode,
            user=user,
            anon_key=anon_key,
            game=game,
        )
    if update_html is None:
        update_html = {}

    by = 'team' if team is not None else ('personal' if user is not None or anon_key else 'admin')
    channel_event = {
        'type': 'task.changed',
        'task': task.id,
        'by': by,
        'game_id': str(game.id) if game is not None else None,
    }
    if reason:
        channel_event['reason'] = reason
    channel_event.update(update_html)
    return channel_event


def track_task_change(
    task,
    team=None,
    current_mode=None,
    update_html=None,
    request=None,
    game=None,
    user=None,
    anon_key=None,
    reason=None,
):
    """
    Notify subscribers after the DB transaction commits so clients never read stale rows.
    Team HTML goes only to the team group; personal HTML goes only to that user.
    Anonymous attempts rely on their POST response, while admin changes go to the game group.
    build_event_task_change runs inside the callback so it reads committed task, hint,
    and attempt state. Model save methods must call this hook after their own row is saved.
    """
    target_games = []
    if game is not None:
        target_games.append(game)
    else:
        target_games = list(
            GameTaskGroup.objects.filter(task_group_id=task.task_group_id).select_related('game')
        )
        target_games = [x.game for x in target_games]

    def event_namespace(g):
        if team is not None:
            return team_track_namespace(g.id, team.pk)
        if user is not None:
            return user_game_track_namespace(user.id, g.id)
        return game_track_namespace(g.id)

    def prepare():
        return {str(g.id): next_track_seq(event_namespace(g)) for g in target_games}

    def send(sequences):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        for g in target_games:
            event_body = build_event_task_change(
                    task,
                    team,
                    current_mode,
                    update_html,
                    request,
                    game=g,
                    user=user,
                    anon_key=anon_key,
                    reason=reason,
                )
            event_body['seq'] = sequences[str(g.id)]
            event_body['seq_namespace'] = event_namespace(g)
            channel_event = envelope_track_message(
                event_body, g.id,
            )
            if team is not None:
                log_realtime_sync(
                    'publish', request=request, game_id=g.id, task_id=task.id,
                    user_id=getattr(user, 'id', None), team_id=team.pk,
                    reason=reason, seq=event_body['seq'],
                    seq_namespace=event_body['seq_namespace'], delivery='team',
                )
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['game_team'](g.id, team.pk),
                    channel_event,
                )
                # Remove after every production instance runs the team-id group version.
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['game_team_legacy'](g.id, team.get_name_hash()),
                    channel_event,
                )
            elif user is not None:
                log_realtime_sync(
                    'publish', request=request, game_id=g.id, task_id=task.id,
                    user_id=user.id, recipient_user_id=user.id, reason=reason,
                    seq=event_body['seq'], seq_namespace=event_body['seq_namespace'],
                    delivery='user',
                )
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['user_game'](user.id, g.id),
                    channel_event,
                )
            elif anon_key:
                # Анонимные страницы не открывают TrackGame; ответ POST уже
                # содержит HTML для текущей вкладки.
                continue
            else:
                log_realtime_sync(
                    'publish', request=request, game_id=g.id, task_id=task.id,
                    reason=reason, seq=event_body['seq'],
                    seq_namespace=event_body['seq_namespace'], delivery='game',
                )
                async_to_sync(channel_layer.group_send)(
                    CHANNEL_GROUPS['game'](g.id),
                    channel_event,
                )

    _schedule_channel_broadcast(send, prepare=prepare)


def track_actor_task_change(
    task,
    *,
    team=None,
    user=None,
    anon_key=None,
    game=None,
    reason='task.state_changed',
    current_mode=None,
    update_html=None,
    request=None,
):
    """Central live-update hook for committed actor+task state mutations."""
    if task is None:
        return
    if team is None and user is None:
        # Anonymous pages have no subscription identity yet.
        return
    game = game or GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return
    track_task_change(
        task,
        team=team,
        user=user,
        anon_key=anon_key,
        game=game,
        reason=reason,
        current_mode=current_mode,
        update_html=update_html,
        request=request,
    )


def track_attempt_change(attempt, *, reason='attempt.changed'):
    """Publish the authoritative task projection after an existing attempt changes."""
    if attempt is None or attempt.task_id is None:
        return
    track_actor_task_change(
        attempt.task,
        team=attempt.team if attempt.team_id else None,
        user=attempt.user if attempt.user_id else None,
        anon_key=attempt.anon_key,
        game=attempt.game,
        reason=reason,
    )
