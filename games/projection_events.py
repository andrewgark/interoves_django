"""SQS refresh for one dirty result projection.

Gameplay marks the release in MySQL, then publishes one message. The body
names the release only. Actor identity is durable in MySQL; Redis only
coalesces wake-up messages.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import boto3
from django.core.cache import caches
from django.utils import timezone

logger = logging.getLogger('application')

EVENT_TTL_SECONDS = 10 * 60
QUEUE_URL_ENV = 'PROJECTION_REFRESH_SQS_QUEUE_URL'


def events_enabled():
    return os.environ.get('PROJECTION_REFRESH_EVENTS', '') == '1'


def _cache():
    return caches['track_revisions']


def _mark_key(game_id, task_group_id):
    return 'projection-refresh-event:{}:{}'.format(game_id, task_group_id)


def _mode_key(game_id, task_group_id):
    return 'projection-refresh-mode:{}:{}'.format(game_id, task_group_id)


def _actors_key(game_id, task_group_id):
    return 'projection-refresh-actors:{}:{}'.format(game_id, task_group_id)


def push_actor_refresh(game_id, task_group_id, actor_filter, revision):
    # Compatibility shim for callers from an older deploy. The durable item
    # is written by mark_projection_dirty() in the source transaction.
    return None


def take_actor_refreshes(game_id, task_group_id):
    return []


def clear_projection_refresh_mark(game_id, task_group_id):
    _cache().delete(_mark_key(game_id, task_group_id))
    _cache().delete(_mode_key(game_id, task_group_id))


def publish_projection_refresh(game_id, task_group_id, *, mode, actor_filter=None, revision=None):
    """Publish one release refresh. A live mark coalesces extra actors."""
    if not events_enabled():
        return False
    if actor_filter is not None:
        push_actor_refresh(game_id, task_group_id, actor_filter, revision)
    mark_key = _mark_key(game_id, task_group_id)
    mode_key = _mode_key(game_id, task_group_id)
    if mode == 'full':
        # A full refresh supersedes actor refreshes already buffered for this
        # release.  The worker reads this marker even when the SQS message
        # itself was originally published with mode=actor.
        _cache().set(mode_key, 'full', timeout=EVENT_TTL_SECONDS)
    else:
        _cache().add(mode_key, 'actor', timeout=EVENT_TTL_SECONDS)
    # ``get`` followed by ``set`` is racy across web/worker instances: two
    # callers can both enqueue a refresh and then contend on the same
    # DailyResultProjectionState row.  RedisCache.add() is backed by SETNX,
    # so only one caller becomes the owner of the in-flight refresh mark.
    if not _cache().add(mark_key, '1', timeout=EVENT_TTL_SECONDS):
        logger.info(
            'projection refresh coalesced game=%s task_group=%s mode=%s',
            game_id, task_group_id, mode,
        )
        return True
    queue_url = os.environ.get(QUEUE_URL_ENV, '').strip()
    if not queue_url:
        clear_projection_refresh_mark(game_id, task_group_id)
        logger.error(
            'projection refresh skipped: queue url missing game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False
    body = {
        'version': 1,
        'type': 'projection.refresh',
        'run_id': str(uuid.uuid4()),
        'scheduled_for': timezone.now().isoformat(),
        'dedupe_key': 'projection.refresh:{}:{}'.format(game_id, task_group_id),
        'payload': {
            'game_id': str(game_id),
            'task_group_id': int(task_group_id),
            'mode': mode,
        },
    }
    try:
        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(body, separators=(',', ':')),
        )
    except Exception:
        clear_projection_refresh_mark(game_id, task_group_id)
        logger.exception(
            'projection refresh failed game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False
    logger.info(
        'projection refresh published game=%s task_group=%s mode=%s',
        game_id, task_group_id, mode,
    )
    return True


def run_named_projection_refresh(*, game_id, task_group_id, mode):
    """Refresh one release. Returns ``missing`` or ``ok``."""
    from games.daily_result_projection import (
        _refresh_actor_by_ids,
        projection_state_is_valid,
        refresh_daily_result_projection,
    )
    from games.models import DailyResultProjectionState, Game, TaskGroup

    game = Game.objects.filter(pk=game_id).first()
    group = TaskGroup.objects.filter(pk=task_group_id).first()
    if game is None or group is None:
        clear_projection_refresh_mark(game_id, task_group_id)
        return 'missing'

    for _pass in range(2):
        if _cache().get(_mode_key(game_id, task_group_id)) == 'full':
            mode = 'full'
        if mode == 'full':
            refresh_daily_result_projection(game, group)
        else:
            from games.daily.projection import _refresh_dirty_actors
            _refresh_dirty_actors(game.pk, group.pk)
        mode = 'actor'
        state = DailyResultProjectionState.objects.filter(game=game, task_group=group).first()
        if projection_state_is_valid(state, game):
            break
    clear_projection_refresh_mark(game_id, task_group_id)
    return 'ok'
