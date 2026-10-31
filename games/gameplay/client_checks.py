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
from django.core.cache import caches

from games.gameplay import offline_telemetry
from games.gameplay.attempts import checker_for_task, client_spec_for_task
from games.gameplay.policy import resolve_capabilities
from games.puzzles import get_puzzle
from games.puzzles.raddle.engine import parse_raddle_data, word_matches

cache = caches['offline_play']

logger = logging.getLogger('games.offline')

CLIENT_VERDICTS = ('Ok', 'Partial', 'Wrong')
# One admin alert per task and kind within this window.
ALERT_THROTTLE_SECONDS = 60 * 60


def instant_verdict_allowed(*, task, game, mode, team=None, user=None, anon_key=None):
    """Whether the page may show ``task``'s verdict before the server confirms it."""
    if not settings.OFFLINE_PLAY_ENABLED:
        return False
    return resolve_capabilities(
        game=game, task_type=task.task_type, mode=mode, team=team, user=user, anon_key=anon_key,
    ).instant_verdict


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
        offline_telemetry.count('pack_build_failed')
        _alert('pack_build_failed', task, game, ['Не удалось собрать проверку для клиента.'])
        return None
    if spec is None:
        return None
    return {
        'spec_json': json.dumps(spec, ensure_ascii=False, separators=(',', ':')),
        'offline_attempts': capabilities.offline_attempts,
    }


def server_verdict(task, move, attempt=None, persisted=False):
    """What the client should have said about ``move``, or None if unknown.

    A puzzle whose verdict depends on the board (Салатик: a path is an answer
    only while that word is unsolved) says it from the checked ``attempt``.
    """
    puzzle = get_puzzle(task.task_type)
    verdict_of = getattr(puzzle, 'client_verdict_of', None) if puzzle is not None else None
    if verdict_of is not None:
        return verdict_of(attempt, persisted) if attempt is not None else None
    if task.task_type == 'raddle':
        parsed = parse_raddle_data(task)
        if not parsed or move.word_index is None or not 0 <= move.word_index < parsed['n_words']:
            return None
        return 'Ok' if word_matches(move.word, parsed['word_accept'][move.word_index]) else 'Wrong'
    checker = checker_for_task(task)
    if checker.client_spec(task) is None:
        return None
    return checker.check(move.text, None).status


def audit_client_verdict(ctx, move, attempt=None, persisted=False):
    """Log and report a browser verdict that differs from the server's."""
    if move.client_verdict is None:
        return
    try:
        expected = server_verdict(ctx.task, move, attempt, persisted)
        if expected is None or expected == move.client_verdict:
            return
        offline_telemetry.count('verdict_divergence')
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


def audit_duplicate_verdict(task_id, post):
    """A page that called a repeat of an earlier move a new correct answer diverged.

    A resent move with its client_event_id is answered before the duplicate
    check, so a duplicate with the browser's 'Ok' is not a retry.
    """
    if post.get('client_verdict') != 'Ok':
        return
    try:
        offline_telemetry.count('verdict_divergence')
        logger.warning(
            'event=offline_verdict_divergence task_id=%s game_id=%s client=Ok server=Duplicate',
            task_id, post.get('game_id', ''),
        )
        from games.models import Game, Task

        task = Task.objects.filter(pk=task_id).first()
        game = Game.objects.filter(pk=post.get('game_id', '')).first()
        if task is not None and game is not None:
            _alert('verdict_divergence', task, game, ['Клиент: <b>Ok</b>, сервер: <b>повтор</b>.'])
    except Exception:
        logger.exception('event=offline_verdict_audit_failed task_id=%s', task_id)


def audit_page_win(task, game, client_verdict, won):
    """A page that showed a win (Алфавитка's final word) the server did not record diverged."""
    if client_verdict != 'Ok' or won:
        return
    try:
        offline_telemetry.count('verdict_divergence')
        logger.warning(
            'event=offline_verdict_divergence task_id=%s task_type=%s game_id=%s client=Ok server=Wrong',
            task.id, task.task_type, game.id,
        )
        _alert('verdict_divergence', task, game, ['Клиент: <b>победа</b>, сервер: <b>не угадано</b>.'])
    except Exception:
        logger.exception('event=offline_verdict_audit_failed task_id=%s', task.id)


def _alert(kind, task, game, lines):
    try:
        if not cache.add('offline_play:alert:{}:{}'.format(kind, task.id), 1, ALERT_THROTTLE_SECONDS):
            return
    except Exception:
        # Without the throttle an outage could flood the admin chat; skip.
        logger.exception('event=offline_play_alert_throttle_failed kind=%s task_id=%s', kind, task.id)
        return
    from games.ops.telegram.bot.notify import notify_admin_offline_play

    notify_admin_offline_play(kind=kind, task=task, game=game, lines=lines)
