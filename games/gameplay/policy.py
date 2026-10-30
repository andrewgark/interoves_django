"""Which client-side and offline features a move may use.

Resolves OfflinePlayRule rows (docs/offline-play-plan.md, «Конфиг») into the
capabilities of one actor in one game, task type and mode. Hard limits in
code come first and no rule lifts them: the OFFLINE_PLAY_ENABLED kill
switch, and team play stays online.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import caches

from games.models.offline import RULES_CACHE_KEY, OfflinePlayRule

cache = caches['offline_play']
logger = logging.getLogger('games.offline')

RULES_CACHE_SECONDS = 300


@dataclass(frozen=True)
class OfflineCapabilities:
    instant_verdict: bool = False
    offline_attempts: bool = False
    offline_timing: bool = False
    offline_hints: bool = False


NO_CAPABILITIES = OfflineCapabilities()


def _cached_rules():
    try:
        return cache.get(RULES_CACHE_KEY)
    except Exception:
        # The cache is an optimization; a Redis outage must not break pages.
        logger.exception('event=offline_play_rules_cache_failed')
        return None


def _rules():
    rules = _cached_rules()
    if rules is None:
        rules = [
            {
                'pk': rule.pk,
                'game_id': rule.game_id or '',
                'task_type': rule.task_type,
                'mode': rule.mode,
                'instant_verdict': rule.instant_verdict,
                'offline_attempts': rule.offline_attempts,
                'offline_timing': rule.offline_timing,
                'offline_hints': rule.offline_hints,
                'rollout_percent': rule.rollout_percent,
                'updated_at': rule.updated_at.timestamp(),
            }
            for rule in OfflinePlayRule.objects.filter(enabled=True)
        ]
        try:
            cache.set(RULES_CACHE_KEY, rules, RULES_CACHE_SECONDS)
        except Exception:
            logger.exception('event=offline_play_rules_cache_failed')
    return rules


def _matches(rule, *, game_id, task_type, mode):
    return (
        rule['game_id'] in ('', game_id)
        and rule['task_type'] in ('', task_type)
        and rule['mode'] in ('', mode)
    )


def _specificity(rule):
    return (bool(rule['game_id']), bool(rule['task_type']), bool(rule['mode']), rule['updated_at'], rule['pk'])


def _in_rollout(rule, actor_key):
    percent = rule['rollout_percent']
    if percent >= 100:
        return True
    digest = hashlib.sha256('{}:{}'.format(rule['pk'], actor_key).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') % 100 < percent


def resolve_capabilities(*, game, task_type, mode, team=None, user=None, anon_key=None):
    """Capabilities of one actor's moves in ``game`` for tasks of ``task_type``."""
    if not settings.OFFLINE_PLAY_ENABLED or game is None or team is not None:
        return NO_CAPABILITIES
    if user is not None:
        actor_key = 'user:{}'.format(user.pk)
    elif anon_key:
        actor_key = 'anon:{}'.format(anon_key)
    else:
        return NO_CAPABILITIES
    matching = [
        rule for rule in _rules()
        if _matches(rule, game_id=game.id, task_type=task_type or '', mode=mode or '')
    ]
    if not matching:
        return NO_CAPABILITIES
    rule = max(matching, key=_specificity)
    if not _in_rollout(rule, actor_key):
        return NO_CAPABILITIES
    # Each capability builds on the previous one.
    instant = rule['instant_verdict']
    attempts = instant and rule['offline_attempts']
    return OfflineCapabilities(
        instant_verdict=instant,
        offline_attempts=attempts,
        offline_timing=attempts and rule['offline_timing'],
        offline_hints=attempts and rule['offline_hints'],
    )
