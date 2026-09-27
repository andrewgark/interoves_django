"""Bulk-safe commands used by the Attempt and PendingAttempt admin pages."""

import json
from collections import defaultdict

from django.db import transaction

from games.models import Attempt, CHAIN_TASK_TYPES, Task
from games.replay_planner import actor_key, queue_chain_replays_for_tasks


def _load_attempts(attempt_ids, *, pending_only=False, lock=False):
    ids = {int(value) for value in attempt_ids}
    queryset = Attempt.manager.filter(pk__in=ids)
    if pending_only:
        queryset = queryset.filter(status='Pending')
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset.select_related('task', 'game').order_by('task_id', 'time', 'pk'))


def _wall_additions(task, attempts):
    payload = json.loads(task.checker_data or '{}')
    for attempt in attempts:
        submitted = json.loads(attempt.text)
        words = sorted(x.lower() for x in submitted['words'])
        for category in payload['answers']:
            if sorted(x.lower() for x in category['words']) != words:
                continue
            explanation = submitted['explanation']
            if explanation not in category['checker'].split('\n'):
                category['checker'] = '{}\n{}'.format(category['checker'], explanation)
            break
    return json.dumps(payload)


def _apply_checker_additions(task, attempts):
    if task.task_type == 'wall':
        task.checker_data = _wall_additions(task, attempts)
        return
    current = task.checker_data or ''
    task.checker_data = current + ''.join('\n{}'.format(a.text) for a in attempts)


def _queue_after_commit(task_ids, selected_keys=None):
    transaction.on_commit(
        lambda ids=tuple(task_ids), keys=selected_keys: queue_chain_replays_for_tasks(
            ids, selected_keys=keys,
        )
    )


def add_attempts_to_checker(attempt_ids, *, pending_only=False, recheck_selected_non_chain=False):
    """Merge selected answers atomically and queue affected chain replays."""
    attempts = _load_attempts(attempt_ids, pending_only=pending_only)
    by_task = defaultdict(list)
    for attempt in attempts:
        if attempt.task_id:
            by_task[attempt.task_id].append(attempt)

    with transaction.atomic():
        task_ids = []
        for task_id, task_attempts in by_task.items():
            task = Task.objects.select_for_update().get(pk=task_id)
            _apply_checker_additions(task, task_attempts)
            task.save(
                skip_semantics_reconciliation=(task.task_type in CHAIN_TASK_TYPES),
            )
            task_ids.append(task_id)
        def after_commit():
            queue_chain_replays_for_tasks(task_ids)
            if recheck_selected_non_chain:
                from games.recheck import recheck
                for attempt in attempts:
                    if attempt.task.task_type not in CHAIN_TASK_TYPES:
                        recheck(None, attempt.pk)

        transaction.on_commit(after_commit)
    return len(attempts)


def accept_pending_attempts(attempt_ids):
    """Accept pending answers, extend the checker, and queue chronological replay."""
    return add_attempts_to_checker(
        attempt_ids,
        pending_only=True,
        recheck_selected_non_chain=True,
    )


def reject_pending_attempts(attempt_ids):
    """Reject pending answers without changing checker data or chain state."""
    from games.views.track import track_attempt_change

    with transaction.atomic():
        attempts = _load_attempts(attempt_ids, pending_only=True, lock=True)
        changed = 0
        for attempt in attempts:
            if not attempt.possible_status:
                continue
            attempt.status = attempt.possible_status
            attempt.save(update_fields=['status'])
            track_attempt_change(attempt, reason='attempt.prestatus_confirmed')
            changed += 1
    return changed


def recheck_chain_attempts(attempt_ids, *, full_task=False):
    """Queue deduplicated chronological replays for selected chain actors."""
    attempts = _load_attempts(attempt_ids)
    task_ids = {a.task_id for a in attempts if a.task_id}
    selected_keys = None if full_task else {
        (a.task_id, a.game_id, actor_key(a))
        for a in attempts
        if a.task_id and a.game_id and a.task.task_type in CHAIN_TASK_TYPES
    }
    with transaction.atomic():
        _queue_after_commit(tuple(task_ids), selected_keys)
    return len(selected_keys or ())


# Backward-compatible name for callers of the first bulk implementation.
add_to_checker = accept_pending_attempts
