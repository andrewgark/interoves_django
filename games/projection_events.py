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


def _daily_statistics_mark_key(game_id, task_group_id):
    return 'daily-statistics-refresh:{}:{}'.format(game_id, task_group_id)


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


def publish_daily_statistics_refresh(game_id, task_group_id, *, revision=None):
    """Coalesce a post-commit statistics snapshot refresh on the existing queue."""
    if not events_enabled():
        return False
    mark_key = _daily_statistics_mark_key(game_id, task_group_id)
    try:
        if not _cache().add(mark_key, str(revision or 0), timeout=EVENT_TTL_SECONDS):
            logger.info(
                'daily_statistics refresh coalesced game=%s task_group=%s',
                game_id, task_group_id,
            )
            return True
    except Exception:
        logger.exception(
            'daily_statistics refresh dedupe failed game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False

    queue_url = os.environ.get(QUEUE_URL_ENV, '').strip()
    if not queue_url:
        _cache().delete(mark_key)
        logger.error(
            'daily_statistics refresh skipped: queue url missing game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False
    body = {
        'version': 1,
        'type': 'daily_statistics.refresh',
        'run_id': str(uuid.uuid4()),
        'scheduled_for': timezone.now().isoformat(),
        'dedupe_key': 'daily_statistics.refresh:{}:{}'.format(game_id, task_group_id),
        'payload': {
            'game_id': str(game_id),
            'task_group_id': int(task_group_id),
            'revision': int(revision or 0),
        },
    }
    try:
        boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        ).send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(body, separators=(',', ':')),
        )
    except Exception:
        _cache().delete(mark_key)
        logger.exception(
            'daily_statistics refresh publish failed game=%s task_group=%s',
            game_id, task_group_id,
        )
        return False
    logger.info(
        'daily_statistics refresh published game=%s task_group=%s revision=%s',
        game_id, task_group_id, revision,
    )
    return True


def run_named_daily_statistics_refresh(*, game_id, task_group_id):
    """Build the current public statistics generation after its source commit."""
    from games.daily.timing.statistics import (
        build_daily_statistics, _cache_revision_key, _versioned_cache_key,
    )
    from games.models import Game, GameTaskGroup, TaskGroup

    game = Game.objects.filter(pk=game_id).first()
    group = TaskGroup.objects.filter(pk=task_group_id).first()
    mark_key = _daily_statistics_mark_key(game_id, task_group_id)
    if game is None or group is None:
        _cache().delete(mark_key)
        return 'missing'

    links = list(GameTaskGroup.objects.filter(
        game=game, task_group=group,
    ).values_list('number', flat=True))
    published_at_values = set()
    if links:
        from games.daily.page.section import publish_at_for

        for number in links:
            published_at_values.add(publish_at_for(game, number))
    else:
        published_at_values.add(None)

    cache = caches['daily_statistics']
    revision_key = _cache_revision_key(game_id, task_group_id)
    for _attempt in range(3):
        revision = int(cache.get(revision_key, 0) or 0)
        for published_at in published_at_values:
            build_daily_statistics(game, group, published_at=published_at)
            result_key = _versioned_cache_key(
                game_id, task_group_id, revision, published_at,
            )
            if cache.get(result_key) is None:
                raise RuntimeError('daily statistics generation was not published')
        current_revision = int(cache.get(revision_key, 0) or 0)
        # Drop the mark before validating the generation. If a source update
        # races this delete, its on-commit publisher can add a fresh mark and
        # message; if it raced earlier, the changed revision forces another pass.
        _cache().delete(mark_key)
        latest_revision = int(cache.get(revision_key, 0) or 0)
        if revision == current_revision == latest_revision:
            logger.info(
                'daily_statistics refresh completed game=%s task_group=%s revision=%s',
                game_id, task_group_id, revision,
            )
            return 'ok'
    logger.warning(
        'daily_statistics refresh superseded game=%s task_group=%s revision=%s',
        game_id, task_group_id, latest_revision,
    )
    return (
        'superseded'
        if publish_daily_statistics_refresh(
            game_id, task_group_id, revision=latest_revision,
        )
        else 'retry'
    )


def run_named_projection_refresh(*, game_id, task_group_id, mode):
    """Refresh one release. Returns ``missing`` or ``ok``."""
    from games.daily_result_projection import (
        _refresh_actor_by_ids,
        projection_state_is_valid,
        refresh_daily_result_projection,
    )
    from games.models import DailyResultProjectionState, Game, TaskGroup
    from games.daily.projection import scorer_adapter_version

    game = Game.objects.filter(pk=game_id).first()
    group = TaskGroup.objects.filter(pk=task_group_id).first()
    if game is None or group is None:
        clear_projection_refresh_mark(game_id, task_group_id)
        return 'missing'

    for _pass in range(2):
        if _cache().get(_mode_key(game_id, task_group_id)) == 'full':
            mode = 'full'
        if mode == 'actor':
            state = DailyResultProjectionState.objects.filter(
                game=game, task_group=group,
            ).first()
            # Actor deltas cannot establish complete coverage for a new,
            # invalidated or semantically outdated projection. Promote this
            # coalesced actor event to one canonical release rebuild.
            if (
                state is None
                or not state.coverage_complete
                or state.full_refresh_required
                or state.adapter_version != scorer_adapter_version(game, group)
            ):
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
    if not projection_state_is_valid(state, game):
        logger.warning(
            'daily_result_projection_refresh_incomplete game=%s task_group=%s mode=%s revision=%s',
            game_id, task_group_id, mode,
            getattr(state, 'source_revision', 'missing'),
        )
        return 'retry'
    return 'ok'
