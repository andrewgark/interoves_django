"""Answer checks in the browser: what a page receives and how its verdicts are audited.

A page gets a task's client spec only when the actor's capabilities allow
an instant verdict (games.gameplay.policy). The move then carries the
browser's verdict, and the server compares it with its own: a divergence
means the spec or a normalization twin is wrong (or the client is forged),
so it is logged and reported to the admin (docs/offline-play-plan.md,
«Наблюдаемость»). Neither path may break the page or the move.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.cache import cache

from games.gameplay.attempts import checker_for_task, client_spec_for_task
from games.gameplay.policy import resolve_capabilities
from games.puzzles.raddle.engine import parse_raddle_data, word_matches

logger = logging.getLogger('games.offline')

CLIENT_VERDICTS = ('Ok', 'Partial', 'Wrong')
# One admin alert per task and kind within this window.
ALERT_THROTTLE_SECONDS = 60 * 60


def page_client_check(*, task, game, mode, team=None, user=None, anon_key=None):
    """``{'spec_json', 'offline_attempts'}`` for a page that may check ``task`` itself, else None."""
    if not settings.OFFLINE_PLAY_ENABLED:
        return None
    capabilities = resolve_capabilities(
        game=game, task_type=task.task_type, mode=mode, team=team, user=user, anon_key=anon_key,
    )
    if not capabilities.instant_verdict:
        return None
    try:
        spec = client_spec_for_task(task)
    except Exception:
        # Broken checker data must not break the page; the server still checks.
        logger.exception('event=offline_pack_build_failed task_id=%s task_type=%s', task.id, task.task_type)
        _alert('pack_build_failed', task, game, ['Не удалось собрать проверку для клиента.'])
        return None
    if spec is None:
        return None
    return {
        'spec_json': json.dumps(spec, ensure_ascii=False, separators=(',', ':')),
        'offline_attempts': capabilities.offline_attempts,
    }


def server_verdict(task, move):
    """What the client spec should have said about ``move``, or None if unknown."""
    if task.task_type == 'raddle':
        parsed = parse_raddle_data(task)
        if not parsed or move.word_index is None or not 0 <= move.word_index < parsed['n_words']:
            return None
        return 'Ok' if word_matches(move.word, parsed['word_accept'][move.word_index]) else 'Wrong'
    checker = checker_for_task(task)
    if checker.client_spec(task) is None:
        return None
    return checker.check(move.text, None).status


def audit_client_verdict(ctx, move):
    """Log and report a browser verdict that differs from the server's."""
    if move.client_verdict is None:
        return
    try:
        expected = server_verdict(ctx.task, move)
        if expected is None or expected == move.client_verdict:
            return
        logger.warning(
            'event=offline_verdict_divergence task_id=%s task_type=%s game_id=%s '
            'word_index=%s client=%s server=%s',
            ctx.task.id, ctx.task.task_type, ctx.game.id, move.word_index, move.client_verdict, expected,
        )
        _alert('verdict_divergence', ctx.task, ctx.game, [
            'Клиент: <b>{}</b>, сервер: <b>{}</b>.'.format(move.client_verdict, expected),
            'Слово: {}'.format('—' if move.word_index is None else move.word_index + 1),
        ])
    except Exception:
        # Auditing is telemetry; the move itself is already checked and saved.
        logger.exception('event=offline_verdict_audit_failed task_id=%s', ctx.task.id)


def _alert(kind, task, game, lines):
    if not cache.add('offline_play:alert:{}:{}'.format(kind, task.id), 1, ALERT_THROTTLE_SECONDS):
        return
    from games.ops.telegram.bot.notify import notify_admin_offline_play

    notify_admin_offline_play(kind=kind, task=task, game=game, lines=lines)
