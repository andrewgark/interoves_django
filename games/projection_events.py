"""SQS refresh for one dirty result projection.

Gameplay marks the release in MySQL, then publishes one message. The body
names the release only. Actor identity stays in Redis until the worker
drains it. A second mark while a message is outstanding is coalesced.
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


def _actors_key(game_id, task_group_id):
    return 'projection-refresh-actors:{}:{}'.format(game_id, task_group_id)


def push_actor_refresh(game_id, task_group_id, actor_filter, revision):
    key = _actors_key(game_id, task_group_id)
    pending = list(_cache().get(key) or [])
    pending.append({'filter': actor_filter, 'revision': revision})
    _cache().set(key, pending, timeout=EVENT_TTL_SECONDS)


def take_actor_refreshes(game_id, task_group_id):
    key = _actors_key(game_id, task_group_id)
    pending = list(_cache().get(key) or [])
    _cache().delete(key)
    return pending


def clear_projection_refresh_mark(game_id, task_group_id):
    _cache().delete(_mark_key(game_id, task_group_id))


def publish_projection_refresh(game_id, task_group_id, *, mode, actor_filter=None, revision=None):
    """Publish one release refresh. A live mark coalesces extra actors."""
    if not events_enabled():
        return False
    if actor_filter is not None:
        push_actor_refresh(game_id, task_group_id, actor_filter, revision)
    mark_key = _mark_key(game_id, task_group_id)
    if _cache().get(mark_key):
        logger.info(
            'projection refresh coalesced game=%s task_group=%s mode=%s',
            game_id, task_group_id, mode,
        )
        return True
    queue_url = os.environ.get(QUEUE_URL_ENV, '').strip()
    if not queue_url:
        logger.error(
            'projection refresh skipped: queue url missing game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False
    _cache().set(mark_key, '1', timeout=EVENT_TTL_SECONDS)
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
        actors = take_actor_refreshes(game_id, task_group_id)
        if mode == 'full' or not actors:
            refresh_daily_result_projection(game, group)
        else:
            for item in actors:
                _refresh_actor_by_ids(
                    game.pk, group.pk, item['filter'],
                    expected_revision=item.get('revision'),
                )
        mode = 'actor'
        state = DailyResultProjectionState.objects.filter(game=game, task_group=group).first()
        if projection_state_is_valid(state, game) and not _cache().get(_actors_key(game_id, task_group_id)):
            break
    clear_projection_refresh_mark(game_id, task_group_id)
    return 'ok'
