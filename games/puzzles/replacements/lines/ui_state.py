"""Shared replacements drafts (``ReplacementsUiState``): merge rules and reads.

Guesses are a map ``{"<line>:<slot>": "guess"}``. Changes are merged slot by
slot, so teammates editing different slots never overwrite each other; for one
slot the last change to reach the server wins. Solved slots never hold drafts.
"""

import json

MAX_GUESS_LENGTH = 200
MAX_CHANGES = 5000


def slot_key(line_index, slot_index):
    return '{}:{}'.format(line_index, slot_index)


def parse_slot_key(key, slot_counts):
    try:
        line_raw, slot_raw = str(key).split(':')
        line_index, slot_index = int(line_raw), int(slot_raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= line_index < len(slot_counts) or not 0 <= slot_index < slot_counts[line_index]:
        return None
    return line_index, slot_index


def _clean_value(value):
    if not isinstance(value, str):
        return None
    return value.strip()[:MAX_GUESS_LENGTH]


def _is_solved(solved_slots, line_index, slot_index):
    return slot_index in (solved_slots or {}).get(line_index, ())


def clean_guesses(raw, slot_counts, solved_slots=None):
    """Drop invalid keys, empty values and solved slots from a stored map."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        parsed = parse_slot_key(key, slot_counts)
        value = _clean_value(value)
        if parsed is None or not value or _is_solved(solved_slots, *parsed):
            continue
        out[slot_key(*parsed)] = value
    return out


def apply_guess_changes(guesses, changes, slot_counts, solved_slots=None):
    """Merge ``changes`` (value, or None/'' to clear) into ``guesses``."""
    merged = clean_guesses(guesses, slot_counts, solved_slots)
    for key, value in (changes or {}).items():
        parsed = parse_slot_key(key, slot_counts)
        if parsed is None or _is_solved(solved_slots, *parsed):
            continue
        normalized = slot_key(*parsed)
        if value is None:
            merged.pop(normalized, None)
            continue
        value = _clean_value(value)
        if value is None:
            continue
        if value:
            merged[normalized] = value
        else:
            merged.pop(normalized, None)
    return merged


def parse_changes(raw):
    """Parse the POSTed ``changes`` JSON object; None when malformed."""
    if raw is None or raw == '':
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or len(data) > MAX_CHANGES:
        return None
    return data


def ui_state_lookup(task, *, game, mode, team=None, user=None, anon_key=None, replay_slot=None):
    from games.models import ReplacementsUiState

    replay_run_id = replay_slot.run_id if replay_slot is not None else None
    actor_key, namespace_key = ReplacementsUiState.identity_keys(
        team=team, user=user, anon_key=anon_key, replay_run_id=replay_run_id,
    )
    return {
        'team': team,
        'user': user,
        'anon_key': anon_key,
        'task': task,
        'game': game,
        'game_mode': mode,
        'replay_slot': replay_slot,
        'replay_run_id': replay_run_id,
        'actor_key': actor_key,
        'namespace_key': namespace_key,
    }


def task_revision(task):
    return str(task.attempt_revision or '')


def stored_guesses(row, task):
    """Guesses of ``row`` for the current task text, and the row revision."""
    if row is None:
        return {}, 0
    if row.task_revision != task_revision(task):
        return {}, int(row.revision or 0)
    return dict(row.guesses or {}), int(row.revision or 0)


def load_guesses(task, *, game, mode, team=None, user=None, anon_key=None, replay_slot=None):
    from games.models import ReplacementsUiState

    if game is None or (team is None and user is None and not anon_key):
        return {}, 0
    lookup = ui_state_lookup(
        task, game=game, mode=mode, team=team, user=user, anon_key=anon_key,
        replay_slot=replay_slot,
    )
    row = (
        ReplacementsUiState.objects
        .filter(**lookup)
        .only('guesses', 'task_revision', 'revision')
        .first()
    )
    return stored_guesses(row, task)


def replacements_ui_payload(task_id, guesses, revision):
    return {
        'replacements_ui': {
            str(task_id): {
                'guesses': dict(guesses or {}),
                'revision': int(revision or 0),
            }
        }
    }
