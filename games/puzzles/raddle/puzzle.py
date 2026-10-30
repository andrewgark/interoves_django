"""Лесенка: board payload and the rules of one submitted word."""

import json
from dataclasses import dataclass

from games.daily.page.share import get_daily_share_adapter
from games.daily.page.state import latest_daily_state
from games.puzzles.core.base import Puzzle
from games.puzzles.core.progress import _int_tuple
from games.puzzles.raddle.board import load_ladder_board
from games.puzzles.raddle.engine import (
    build_raddle_ui_context,
    is_raddle_in_game_assist_hint,
    load_raddle_state,
    parse_raddle_data,
)
from games.puzzles.raddle.submission import (
    accept_ui_draft,
    assist_block,
    attempt_limit_message,
    blocks_duplicate,
    duplicate_fields,
    include_task_html,
    parse_move,
    posted_word_index,
    stale_payload,
    write_move_result,
)


def results_max_points(task, m):
    """Green-cell maximum: every word except the two ends, times the multiplier."""
    parsed = parse_raddle_data(task)
    if parsed:
        middle = max(0, parsed['n_words'] - 2)
        if middle > 0:
            return m * middle
    return m


def submission_opened(attempt, solved_before):
    """The submitted word is newly solved. Ladder ends already solved do not count."""
    try:
        payload = json.loads(getattr(attempt, 'text', None) or '')
        word_index = int(payload.get('word_index', -1))
        state = json.loads(getattr(attempt, 'state', None) or '')
        solved_now = set(_int_tuple(state.get('solved_indices')))
    except (TypeError, ValueError, AttributeError):
        return False, None
    opened = word_index >= 0 and word_index in solved_now and word_index not in solved_before
    return opened, solved_now


def is_complete(task, state_raw):
    """True when every ladder word is solved."""
    if not state_raw:
        return False
    try:
        parsed = parse_raddle_data(task)
        if not parsed:
            return False
        state = load_raddle_state(state_raw, parsed['n_words'])
        return len(set(state.get('solved_indices') or [])) >= parsed['n_words']
    except Exception:
        return False


def attempt_advanced(attempt, last_state):
    """True when this answer added a solved ladder word. Bad JSON does not count."""
    try:
        before = json.loads(last_state or '{}')
        after = json.loads(getattr(attempt, 'state', None) or '{}')
    except (TypeError, ValueError):
        return False
    return bool(
        set(after.get('solved_indices') or [])
        - set(before.get('solved_indices') or [])
    )


def attempt_answer(attempt):
    """The ladder word the attempt was scored against."""
    try:
        payload = json.loads(attempt.text)
        idx = int(payload.get('word_index', -1))
    except (ValueError, TypeError):
        return attempt.task.answer or ''
    parsed = parse_raddle_data(attempt.task)
    if parsed and 0 <= idx < parsed['n_words']:
        return parsed['words'][idx]
    return '—'


def pretty_text(attempt):
    """List label for one ladder word."""
    try:
        payload = json.loads(attempt.text)
        idx = int(payload.get('word_index', -1))
        word = payload.get('word', '')
        if idx >= 0:
            return 'Слово {}: {}'.format(idx + 1, word)
        return str(word) or attempt.text
    except (ValueError, TypeError):
        return attempt.text


def state_summary(state):
    """Admin one-line summary of a raddle chain."""
    solved = len(state.get('solved_indices', []))
    total = state.get('total', '?')
    return '{} words solved ({} pts)'.format(solved, total)


def read_post(post, task):
    """Read a ladder POST. The view still owns the stale-submit check."""
    del task
    return parse_move(post)


def write_response(response):
    """Copy the ladder verdict onto the JSON body. The view still owns HTML."""
    return write_move_result(
        response.result,
        response.attempt.task,
        response.attempt,
        response.word_index,
        response.word,
    )


def build_raddle_board_data(
    *,
    game,
    task,
    placement,
    parsed,
    state,
    attempts,
    hint_attempts,
    mode,
    ui_state,
    share_title,
    user,
    anon_key,
):
    """Build Raddle's board payload and attach its share-card payload."""
    if ui_state is not None:
        state['drafts'] = dict(ui_state.drafts or {})
        state['clue_marks'] = dict(ui_state.clue_marks or {})
    ui = build_raddle_ui_context(
        parsed,
        state,
        attempts,
        max_attempts=task.get_max_attempts(),
        mode=mode,
        hint_attempts=hint_attempts,
    )
    get_daily_share_adapter(game.id, fallback_key='ladder').attach(
        ui,
        parsed=parsed,
        state=state,
        hint_attempts=hint_attempts,
        game=game,
        task=task,
        placement=placement,
        user=user,
        anon_key=anon_key,
        attempts=attempts,
        share_title=share_title,
    )
    return {
        'parsed': parsed,
        'ui': ui,
        'max_attempts': task.get_max_attempts(),
        'max_points_total': task.get_results_max_points(),
    }


@dataclass
class RaddleBoardSource:
    """Everything a ladder board is drawn from, loaded once.

    ``build(state)`` draws the board of any state of this ladder for the same
    actor: the page draws the current state, the offline pack the reachable
    ones.
    """

    game: object
    task: object
    placement: object
    parsed: dict
    state: dict
    board: object
    mode: str
    share_title: object
    team: object
    user: object
    anon_key: object

    def build(self, state=None):
        data = build_raddle_board_data(
            game=self.game,
            task=self.task,
            placement=self.placement,
            parsed=self.parsed,
            state=dict(self.state if state is None else state),
            attempts=self.board.attempts,
            hint_attempts=self.board.hint_attempts,
            mode=self.mode,
            ui_state=self.board.ui_state,
            share_title=self.share_title,
            user=self.user,
            anon_key=self.anon_key,
        )
        from games.puzzles.raddle.clue_likes import attach_clue_reactions
        attach_clue_reactions(
            data.get('ui') or {}, self.task, team=self.team, user=self.user, anon_key=self.anon_key,
        )
        return data


def raddle_board_source(
    *, game, task, placement, attempts_info, team, user, anon_key, mode, replay_slot, share_title,
):
    """Load the ladder, its actor's current state and attempts; None if unparsable."""
    parsed = parse_raddle_data(task)
    if not parsed:
        return None
    if attempts_info is None:
        preloaded_attempts = None
        preloaded_hints = None
    else:
        preloaded_attempts = attempts_info.attempts
        preloaded_hints = attempts_info.hint_attempts
    board = load_ladder_board(
        game=game,
        task=task,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts=preloaded_attempts,
        hint_attempts=preloaded_hints,
    )
    state = latest_daily_state(
        task,
        game,
        attempts_info,
        default_state=load_raddle_state(None, parsed['n_words']),
        decode_state=lambda raw: load_raddle_state(raw, parsed['n_words']),
        resolve_chain_state=lambda task, game, **kwargs: board.chain,
        chain_state_kwargs={
            'team': team,
            'user': user,
            'anon_key': anon_key,
            'mode': mode,
            'replay_slot': replay_slot,
        },
    )
    return RaddleBoardSource(
        game=game, task=task, placement=placement, parsed=parsed, state=state, board=board,
        mode=mode, share_title=share_title, team=team, user=user, anon_key=anon_key,
    )


def prepare_raddle_board_data(
    *,
    game,
    task,
    placement,
    attempts_info,
    team,
    user,
    anon_key,
    mode,
    replay_slot,
    resolve_chain_state,
    raddle_ui_state_for_actor,
    share_title,
    **ignored,
):
    """Parse, resolve, and build one Raddle board payload."""
    source = raddle_board_source(
        game=game, task=task, placement=placement, attempts_info=attempts_info, team=team,
        user=user, anon_key=anon_key, mode=mode, replay_slot=replay_slot, share_title=share_title,
    )
    if source is None:
        return None
    data = source.build()
    from games.gameplay.client_checks import page_client_check
    offline = page_client_check(
        task=task, game=game, mode=mode, team=team, user=user, anon_key=anon_key,
    )
    if offline is not None:
        data['offline'] = {**offline, 'run': offline_run(replay_slot)}
    return data


def offline_run(replay_slot):
    """Which run an offline pack belongs to: the official game or one replay.

    The official game and every replay share the task revision, but a pack
    built for one run lacks the pieces of another.
    """
    return str(replay_slot.run_id) if replay_slot is not None else 'official'


def player_hints(hints):
    """Hints shown to the player. In-game clue and answer rows stay hidden."""
    return [hint for hint in hints if not is_raddle_in_game_assist_hint(hint)]


def merge_state(task, source_json, target_json):
    """Union two ladder chains. The raw strings are loaded again, not the parsed dicts."""
    try:
        source = json.loads(source_json or '{}')
        target = json.loads(target_json or '{}')
    except (TypeError, ValueError):
        return None
    if not isinstance(source, dict) or not isinstance(target, dict):
        return None
    from games.puzzles.raddle.engine import (
        dump_raddle_state,
        resolve_assist_tiers,
        word_solve_credit,
    )
    parsed = parse_raddle_data(task)
    if not parsed:
        return None
    source_state = load_raddle_state(source_json, parsed['n_words'])
    target_state = load_raddle_state(target_json, parsed['n_words'])
    solved = sorted(
        set(source_state.get('solved_indices') or [])
        | set(target_state.get('solved_indices') or [])
    )
    used_hints = sorted(
        set(source_state.get('used_hints') or [])
        | set(target_state.get('used_hints') or [])
    )
    assist = resolve_assist_tiers(source_state)
    for index, tier in resolve_assist_tiers(target_state).items():
        assist[index] = max(assist.get(index, 0), tier)
    total = sum(
        word_solve_credit(assist.get(index, 0), parsed.get('assist') or {})
        for index in solved
        if index not in (0, parsed['n_words'] - 1)
    )
    return json.dumps(dump_raddle_state({
        'solved_indices': solved,
        'used_hints': used_hints,
        'assist_tier': {str(index): tier for index, tier in assist.items()},
        'total': float(total),
        'drafts': dict(source_state.get('drafts') or {}, **(target_state.get('drafts') or {})),
        'clue_marks': dict(
            source_state.get('clue_marks') or {},
            **(target_state.get('clue_marks') or {}),
        ),
    }, parsed['n_words']), ensure_ascii=False)


def skips_hint_penalty(hint):
    """An in-game clue or answer does not add a penalty and is not a taken hint."""
    return is_raddle_in_game_assist_hint(hint)


class RaddlePuzzle(Puzzle):
    task_type = 'raddle'
    checker_id = 'raddle'
    report_attempt_status = False

    build_board = staticmethod(build_raddle_board_data)
    prepare_board = staticmethod(prepare_raddle_board_data)
    parse_move = staticmethod(parse_move)
    read_post = staticmethod(read_post)
    load_board = staticmethod(load_ladder_board)
    assist_block = staticmethod(assist_block)
    accept_ui_draft = staticmethod(accept_ui_draft)
    posted_word_index = staticmethod(posted_word_index)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    stale_payload = staticmethod(stale_payload)
    duplicate_fields = staticmethod(duplicate_fields)
    write_move_result = staticmethod(write_move_result)
    write_response = staticmethod(write_response)
    include_task_html = staticmethod(include_task_html)
    player_hints = staticmethod(player_hints)
    merge_state = staticmethod(merge_state)
    skips_hint_penalty = staticmethod(skips_hint_penalty)
    results_max_points = staticmethod(results_max_points)
    submission_opened = staticmethod(submission_opened)
    is_complete = staticmethod(is_complete)
    attempt_advanced = staticmethod(attempt_advanced)
    attempt_answer = staticmethod(attempt_answer)
    pretty_text = staticmethod(pretty_text)
    state_summary = staticmethod(state_summary)
