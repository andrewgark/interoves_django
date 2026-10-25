"""Tournament quotas counted from posted attempt text."""

from __future__ import annotations

import json


def json_field_quota(attempt, revision_attempts, task, team, *, field, label):
    """Quota for one integer JSON field. None means that field still has tries.

    The message stays ``for {label} N``, with N one past the stored index.
    """
    try:
        current_payload = json.loads(attempt.text)
        current = int(current_payload.get(field, -1))
    except (ValueError, TypeError):
        current = -1
    n_attempts = 0
    for previous in revision_attempts:
        try:
            payload = json.loads(previous.text)
            if int(payload.get(field, -1)) == current:
                n_attempts += 1
        except (ValueError, TypeError):
            pass
    max_attempts = task.get_max_attempts()
    if n_attempts >= max_attempts:
        return 'Team {} exceeds attempts limit ({}) in task {} for {} {}'.format(
            team, max_attempts, task, label, current + 1,
        )
    return None


def revision_quota_message(attempt, revision_attempts, task, team):
    """Quota for every attempt in this revision. None means tries remain."""
    n_attempts = len(revision_attempts)
    max_attempts = task.get_max_attempts()
    if n_attempts >= max_attempts:
        return 'Team {} exceeds attempts limit ({}) in task {}'.format(
            team, max_attempts, task,
        )
    return None
