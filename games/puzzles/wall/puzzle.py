"""Стена: one posted group. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.core.base import Puzzle
from games.puzzles.core.primitives import MoveRefusal, TextMove, same_text_verdict
from games.puzzles.core.progress import _wall_groups


def parse_move(post):
    """Read a wall POST. Statuses are the existing JSON codes."""
    stage = post.get('stage')
    if stage not in ('cat_words', 'cat_explanation'):
        return MoveRefusal('invalid_form')
    try:
        if 'text' not in post:
            words = post.getlist('words[]')
            request_data = {'words': sorted(words), 'stage': stage}
        else:
            words = json.loads(post.get('words', ''))
            if not isinstance(words, list):
                raise ValueError('wall words must be a list')
            request_data = {
                'explanation': (post.get('text') or '').strip(),
                'words': words,
                'stage': stage,
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        return MoveRefusal('invalid_form')
    if not request_data['words'] or any(not isinstance(word, str) for word in request_data['words']):
        return MoveRefusal('invalid_form')
    if stage == 'cat_explanation' and not request_data.get('explanation'):
        return MoveRefusal('empty')
    return TextMove(json.dumps(request_data))


def attempt_limit_message(attempt, revision_attempts, task, team, *, attempts=None, chain_state=None):
    """Tournament quota for one wall stage. None means the stage still has tries."""
    validation = task.get_wall().validate_max_attempts(
        attempts, attempt, current_state=chain_state,
    )
    if validation is None:
        return None
    stage, _n_attempts, max_attempts = validation
    return 'Team {} exceeds attempts limit ({}) in wall task {} on stage {}'.format(
        team, max_attempts, task, stage,
    )


def blocks_duplicate(attempt_text, other_text, *, task, state_raw, other_attempt=None):
    return same_text_verdict(attempt_text, other_text, other_attempt)


def progress_signature(state):
    """Solved wall groups. Points and hints are not part of the signature."""
    return (
        'wall',
        _wall_groups(state.get('guessed_words')),
        _wall_groups(state.get('guessed_explanations')),
    )


def pretty_text(attempt):
    """List label for a wall group, including media the wall itself renders."""
    from games.models import AudioManager, ImageManager
    try:
        return attempt.task.get_wall().get_attempt_text(
            json.loads(attempt.text),
            ImageManager(),
            AudioManager(),
        )
    except Exception:
        return attempt.text


def merge_state(task, source_json, target_json):
    """Union two wall chains. A longer word list wins, and stray explanations drop."""
    try:
        source = json.loads(source_json or '{}')
        target = json.loads(target_json or '{}')
    except (TypeError, ValueError):
        return None
    if not isinstance(source, dict) or not isinstance(target, dict):
        return None

    def union_rows(primary, secondary):
        result = list(primary or [])
        for value in secondary or []:
            if value not in result:
                result.append(value)
        return result

    source_words = source.get('guessed_words') or []
    target_words = target.get('guessed_words') or []
    primary, secondary = (
        (source, target) if len(source_words) >= len(target_words) else (target, source)
    )
    guessed_words = union_rows(primary.get('guessed_words'), secondary.get('guessed_words'))
    guessed_explanations = union_rows(
        primary.get('guessed_explanations'), secondary.get('guessed_explanations'),
    )
    guessed_explanations = [row for row in guessed_explanations if row in guessed_words]
    try:
        checker_data = json.loads(task.checker_data or '{}')
        points_words = float(checker_data.get('points_words', 0) or 0)
        points_explanation = float(checker_data.get('points_explanation', 0) or 0)
        points_bonus = float(checker_data.get('points_bonus', 0) or 0)
        answers_count = len(checker_data.get('answers') or [])
        best_points = len(guessed_words) * points_words + len(guessed_explanations) * points_explanation
        if answers_count and len(guessed_words) >= answers_count and len(guessed_explanations) >= answers_count:
            best_points += points_bonus
            best_status = 'Ok'
        else:
            best_status = 'Partial' if best_points else 'Wrong'
    except (TypeError, ValueError):
        best_points = max(source.get('best_points', 0) or 0, target.get('best_points', 0) or 0)
        best_status = primary.get('best_status') or 'Partial'
    return json.dumps({
        'best_status': best_status,
        'best_points': best_points,
        'guessed_words': guessed_words,
        'guessed_explanations': guessed_explanations,
        'last_attempt': primary.get('last_attempt') or secondary.get('last_attempt') or {},
    }, ensure_ascii=False)


def show_status(attempt):
    """List status. A review stays Pending; otherwise the wall's last step."""
    if attempt.status == 'Pending':
        return 'Pending'
    return json.loads(attempt.state)['last_attempt']['status']


def diff_points(attempt):
    """Point increment shown beside a wall attempt, including one still in review."""
    return json.loads(attempt.state)['last_attempt']['points']


def state_summary(state):
    """Admin one-line summary of a wall chain."""
    pts = state.get('best_points', 0)
    stage = state.get('last_attempt', {}).get('stage', '?')
    guessed = len(state.get('guessed_words', []))
    return 'stage={}, pts={}, guessed={} cats'.format(stage, pts, guessed)


def results_max_points(task, m):
    """Green-cell maximum: the wall's own scale times the task multiplier."""
    try:
        base = getattr(task.get_wall(), 'max_points', None)
        if base is not None:
            return float(base) * m
    except Exception:
        pass
    return m


class WallPuzzle(Puzzle):
    task_type = 'wall'
    checker_id = None

    parse_move = staticmethod(parse_move)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    progress_signature = staticmethod(progress_signature)
    pretty_text = staticmethod(pretty_text)
    show_status = staticmethod(show_status)
    merge_state = staticmethod(merge_state)
    diff_points = staticmethod(diff_points)
    state_summary = staticmethod(state_summary)
    results_max_points = staticmethod(results_max_points)
