import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from games.models import Attempt, ChainTaskState, CHAIN_TASK_TYPES, GameTaskGroup, Team
from games.views.views import check_attempt
from games.views.track import track_actor_task_change, track_attempt_change


def recheck(_, attempt_id, *, notify=True):
    attempt = get_object_or_404(Attempt, id=attempt_id)
    try:
        check_attempt(attempt)
        attempt.skip = False
        attempt.save()
    except Exception as e:
        print('SKIP Attempt {} while RECHECKING'.format(attempt))
        print('REASON: {}'.format(e))
        attempt.skip = True
        attempt.save()
    if notify:
        track_attempt_change(attempt, reason='attempt.rechecked')
    return attempt


def _recheck_many(attempts, *, reason):
    changed = {}
    for source in list(attempts):
        attempt = recheck(None, source.id, notify=False)
        key = (
            attempt.task_id,
            attempt.game_id,
            attempt.team_id,
            attempt.user_id,
            attempt.anon_key,
        )
        changed[key] = attempt
    for attempt in changed.values():
        track_attempt_change(attempt, reason=reason)
    return list(changed.values())


def recheck_full(_, attempt_id=None, task=None):
    if task is None:
        task = get_object_or_404(Attempt, id=attempt_id).task
    return _recheck_many(
        Attempt.manager.get_all_task_attempts(task=task, exclude_skip=False),
        reason='task.rechecked_full',
    )


def recheck_queue_from_this(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    return _recheck_many(Attempt.manager.get_all_attempts_after_equal(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
        user=this_attempt.user if this_attempt.user_id else None,
        anon_key=this_attempt.anon_key,
        game=this_attempt.game,
    ), reason='task.rechecked_from_attempt')


def recheck_queue_from_next(_, attempt_id):
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    return _recheck_many(Attempt.manager.get_all_attempts_after(
        team=this_attempt.team, task=this_attempt.task,
        time=this_attempt.time, exclude_skip=False,
        user=this_attempt.user if this_attempt.user_id else None,
        anon_key=this_attempt.anon_key,
        game=this_attempt.game,
    ), reason='task.rechecked_after_attempt')


def recheck_team_task_all_chronological(_, attempt_id):
    """
    Перепроверить все посылки того же актора (команда / личный / аноним), что и у выбранной,
    по тому же заданию, в хронологическом порядке.

    For chain tasks (wall, replacements_lines) delegates to recheck_chain_task which
    replays the whole chain in a single transaction in O(N) without per-attempt DB reads.
    """
    this_attempt = get_object_or_404(Attempt, id=attempt_id)
    task = this_attempt.task
    team = this_attempt.team
    user = this_attempt.user if this_attempt.user_id else None
    anon_key = this_attempt.anon_key

    if task.task_type in CHAIN_TASK_TYPES:
        recheck_chain_task(
            task=task, team=team, user=user, anon_key=anon_key, game=this_attempt.game,
        )
        return

    attempts = Attempt.manager.get_all_attempts(
        team, task, exclude_skip=False, user=user, anon_key=anon_key,
    )
    return _recheck_many(attempts, reason='task.rechecked_chronological')


def recheck_chain_task(task, team=None, user=None, anon_key=None, game=None, *, notify=True):
    """
    Optimised full recheck for wall / replacements_lines.

    Replays ALL attempts for one actor+task pair in a single transaction:
    - One DB read for all attempts.
    - State carried in memory between attempts; no per-attempt DB round-trip.
    - Both game_mode buckets (general / tournament) are rebuilt in one pass.
    - ChainTaskState rows are locked at the start so concurrent submissions
      are blocked until recheck completes.
    - Each Attempt.state is updated in the DB as the audit trail.
    """
    from games.check import CheckerFactory

    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise ValueError('recheck_chain_task: pass game= for tasks in multiple games')

    with transaction.atomic():
        # Lock (and create if missing) both possible ChainTaskState rows upfront.
        for mode in ('general', 'tournament'):
            ChainTaskState.objects.get_or_create(
                team=team, user=user, anon_key=anon_key,
                task=task, game=game, game_mode=mode,
                defaults={'state': None},
            )
        locked_rows = {
            row.game_mode: row
            for row in ChainTaskState.objects.select_for_update().filter(
                team=team, user=user, anon_key=anon_key, task=task, game=game,
            )
        }
        # Reset both chains.
        for row in locked_rows.values():
            row.state = None
            row.last_attempt = None

        checker_type = task.get_checker()
        checker_data = task.checker_data or ''

        # current in-memory chain state per game_mode
        states = {'general': None, 'tournament': None}

        attempts = Attempt.manager.get_all_attempts(
            team, task, exclude_skip=False, user=user, anon_key=anon_key, game=game,
        )

        for attempt in attempts:
            mode = game.get_current_mode(attempt)
            last_state = states[mode]
            try:
                from games.models import CheckerType as CT
                if task.task_type == 'replacements_lines':
                    ct = CT.objects.get(id='replacements_lines')
                elif task.task_type == 'raddle':
                    ct = CT.objects.get(id='raddle')
                else:
                    ct = checker_type
                checker = CheckerFactory().create_checker(ct, checker_data, last_state)
                result = checker.check(attempt.text, attempt)
                attempt.status = result.status
                attempt.points = Decimal(str(result.points or 0))
                if task.task_type != 'word_salad':
                    attempt.points *= task.get_points()
                attempt.state = result.state
                if task.task_type == 'word_salad':
                    attempt.comment = result.comment
                attempt.skip = False
            except Exception as e:
                print('SKIP Attempt {} while RECHECKING chain'.format(attempt))
                print('REASON: {}'.format(e))
                attempt.skip = True
                attempt.state = last_state  # preserve previous state so chain continues
            attempt.save()

            if not attempt.skip:
                states[mode] = attempt.state
                if mode in locked_rows:
                    locked_rows[mode].state = attempt.state
                    locked_rows[mode].last_attempt = attempt

        # Persist updated ChainTaskState rows.
        for row in locked_rows.values():
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    if notify:
        track_actor_task_change(
            task,
            team=team,
            user=user,
            anon_key=anon_key,
            game=game,
            reason='task.chain_rechecked',
        )


def _word_salad_actor_keys(task, game):
    keys = set()
    for qs in (
        Attempt.manager.filter(task=task, game=game).values_list('team_id', 'user_id', 'anon_key'),
        ChainTaskState.objects.filter(task=task, game=game).values_list(
            'team_id', 'user_id', 'anon_key',
        ),
    ):
        for team_id, user_id, anon_key in qs:
            keys.add((team_id, user_id, anon_key or None))
    return keys


def _resolve_word_salad_actor(team_id, user_id, anon_key):
    team = Team.objects.filter(pk=team_id).first() if team_id else None
    if team_id and team is None:
        return None
    user = User.objects.filter(pk=user_id).first() if user_id else None
    if user_id and user is None:
        return None
    return {'team': team, 'user': user, 'anon_key': anon_key}


def _word_salad_attempts(task, *, team=None, user=None, anon_key=None, game=None):
    queryset = Attempt.manager._filter_by_actor(
        Attempt.manager.filter(task=task),
        team=team,
        user=user,
        anon_key=anon_key,
    )
    if game is not None:
        queryset = queryset.filter(game=game)
    return list(queryset.order_by('time', 'id'))


def _expand_salad_active_for_path(last_state, text):
    from games.word_salad import dump_state, load_state

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    action = (payload.get('action') or 'solve').strip().lower()
    if action == 'hint':
        return None
    try:
        path = [int(value) for value in payload.get('path', [])]
    except (TypeError, ValueError):
        return None
    needed = [index for index in path if 0 <= index < 16]
    if not needed:
        return None
    state = load_state(last_state)
    active = set(state.get('active') or [])
    if all(index in active for index in needed):
        return None
    state['active'] = sorted(active | set(needed))
    return dump_state(state)


def _check_word_salad_attempt(checker_type, checker_data, last_state, attempt, *, expand_active=False):
    from games.check import CheckerFactory
    from games.word_salad import (
        EXTRA_FOUND_COMMENT,
        RARE_FOUND_COMMENT,
        dump_state,
        load_state,
        score_for_state,
    )

    checker = CheckerFactory().create_checker(checker_type, checker_data, last_state)
    result = checker.check(attempt.text, attempt)
    if not expand_active or result.status != 'Wrong':
        return result
    expanded = _expand_salad_active_for_path(last_state, attempt.text)
    if expanded is None:
        return result
    retry = CheckerFactory().create_checker(checker_type, checker_data, expanded).check(
        attempt.text, attempt,
    )
    if retry.status not in ('Ok', 'Partial'):
        return result
    if (retry.comment or '') in (RARE_FOUND_COMMENT, EXTRA_FOUND_COMMENT):
        merged = load_state(retry.state)
        original = load_state(last_state)
        merged['active'] = original.get('active', merged.get('active'))
        retry.state = dump_state(merged)
        retry.points = score_for_state(merged)
    return retry


def _apply_word_salad_check_result(attempt, result):
    attempt.status = result.status
    attempt.points = Decimal(str(result.points or 0))
    attempt.state = result.state
    attempt.comment = result.comment
    attempt.skip = False


def _replay_word_salad_attempts(
    task,
    game,
    locked_rows,
    attempts,
    *,
    expand_active=False,
    persist=True,
):
    checker_type = task.get_checker()
    checker_data = task.checker_data or ''
    for row in locked_rows.values():
        row.state = None
        row.last_attempt = None
    states = {'general': None, 'tournament': None}
    last_by_mode = {}

    for attempt in attempts:
        mode = game.get_current_mode(attempt)
        last_state = states[mode]
        try:
            result = _check_word_salad_attempt(
                checker_type,
                checker_data,
                last_state,
                attempt,
                expand_active=expand_active,
            )
            _apply_word_salad_check_result(attempt, result)
        except Exception as exc:
            print('SKIP Attempt {} while RECHECKING word salad'.format(attempt))
            print('REASON: {}'.format(exc))
            attempt.skip = True
            attempt.state = last_state
        if persist:
            attempt.save()
        if not attempt.skip:
            states[mode] = attempt.state
            last_by_mode[mode] = attempt
            if persist and mode in locked_rows:
                locked_rows[mode].state = attempt.state
                locked_rows[mode].last_attempt = attempt
    return states, last_by_mode


def _credit_new_word_salad_answers(
    task,
    *,
    team=None,
    user=None,
    anon_key=None,
    game=None,
    last_ok_times=None,
    attempts=None,
):
    from games.word_salad import find_paths, load_state, parse_task_payload

    last_ok_times = last_ok_times or {}
    grid, words, _rare_words = parse_task_payload(task.checker_data, task.answer or '')
    created = []

    for mode, last_ok_time in last_ok_times.items():
        if last_ok_time is None:
            continue
        preview = [
            attempt for attempt in attempts
            if not attempt.skip and game.get_current_mode(attempt) == mode and attempt.time <= last_ok_time
        ]
        seen_texts = {attempt.text for attempt in preview}
        states, _last = _replay_word_salad_attempts(
            task,
            game,
            {},
            preview,
            expand_active=True,
            persist=False,
        )
        state = load_state(states.get(mode))
        solved = set(state.get('solved_indices') or [])
        if len(solved) >= len(words):
            continue
        active_now = state.get('active') or []
        for index, word in enumerate(words):
            if index in solved:
                continue
            paths = find_paths(grid, word, active=active_now, limit=1)
            if not paths:
                paths = find_paths(grid, word, active=range(16), limit=1)
            if not paths:
                continue
            text = json.dumps({'action': 'solve', 'path': paths[0]}, ensure_ascii=False)
            if text in seen_texts:
                continue
            attempt = Attempt(
                team=team,
                user=user,
                anon_key=anon_key,
                task=task,
                game=game,
                text=text,
                status='Pending',
                points=0,
            )
            attempt.save()
            Attempt.manager.filter(pk=attempt.pk).update(time=last_ok_time)
            attempt.time = last_ok_time
            created.append(attempt)
            seen_texts.add(text)
            solved.add(index)
    return created


def recheck_word_salad_actor(
    task,
    *,
    team=None,
    user=None,
    anon_key=None,
    game=None,
    notify=True,
):
    """Rebuild one actor's Word Salad chain and credit new required words at last Ok time."""
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise ValueError('recheck_word_salad_actor: pass game= for tasks in multiple games')

    credited = 0
    with transaction.atomic():
        for mode in ('general', 'tournament'):
            ChainTaskState.objects.get_or_create(
                team=team, user=user, anon_key=anon_key,
                task=task, game=game, game_mode=mode,
                defaults={'state': None},
            )
        locked_rows = {
            row.game_mode: row
            for row in ChainTaskState.objects.select_for_update().filter(
                team=team, user=user, anon_key=anon_key, task=task, game=game,
            )
        }
        attempts = _word_salad_attempts(
            task, team=team, user=user, anon_key=anon_key, game=game,
        )
        last_ok_times = {}
        for attempt in attempts:
            if attempt.skip or attempt.status != 'Ok':
                continue
            last_ok_times[game.get_current_mode(attempt)] = attempt.time

        if last_ok_times:
            created = _credit_new_word_salad_answers(
                task,
                team=team,
                user=user,
                anon_key=anon_key,
                game=game,
                last_ok_times=last_ok_times,
                attempts=attempts,
            )
            credited = len(created)
            if created:
                attempts = _word_salad_attempts(
                    task, team=team, user=user, anon_key=anon_key, game=game,
                )

        _replay_word_salad_attempts(
            task,
            game,
            locked_rows,
            attempts,
            expand_active=True,
            persist=True,
        )
        for row in locked_rows.values():
            row.save(update_fields=['state', 'last_attempt', 'updated_at'])

    if notify:
        track_actor_task_change(
            task,
            team=team,
            user=user,
            anon_key=anon_key,
            game=game,
            reason='task.word_salad_rechecked',
        )
    return {'credited': credited, 'attempts': len(attempts)}


def recheck_word_salad_task(task, *, game=None, notify=True):
    """Recheck every actor on a Word Salad task, keeping completed times stable."""
    if task.task_type != 'word_salad':
        raise ValueError('recheck_word_salad_task only supports word_salad tasks')
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise ValueError('recheck_word_salad_task: pass game= for tasks in multiple games')

    stats = {'actors': 0, 'credited': 0, 'attempts': 0}
    for team_id, user_id, anon_key in sorted(
        _word_salad_actor_keys(task, game),
        key=lambda item: (item[0] or '', item[1] or 0, item[2] or ''),
    ):
        actor = _resolve_word_salad_actor(team_id, user_id, anon_key)
        if actor is None:
            continue
        result = recheck_word_salad_actor(task, game=game, notify=notify, **actor)
        stats['actors'] += 1
        stats['credited'] += result['credited']
        stats['attempts'] += result['attempts']
    return stats


def sync_word_salad_finds(
    task,
    raw_words,
    *,
    team=None,
    user=None,
    anon_key=None,
    game=None,
):
    """Persist localStorage finds against the current answer/rare/extra lists."""
    from games.word_salad import (
        EXTRA_FOUND_COMMENT,
        RARE_FOUND_COMMENT,
        _FOUND_EXTRA_LIMIT,
        classify_side_finds,
        find_paths,
        load_state,
        normalize_word,
        parse_task_payload,
    )

    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise ValueError('sync_word_salad_finds: pass game= for tasks in multiple games')

    words_in = []
    seen = set()
    for raw in raw_words or []:
        word = normalize_word(raw)
        if not word or word in seen:
            continue
        seen.add(word)
        words_in.append(word)
        if len(words_in) >= _FOUND_EXTRA_LIMIT:
            break

    credited = {'extra': [], 'rare': [], 'answer': []}
    if task.task_type != 'word_salad' or not words_in:
        return {'credited': credited, 'state': None}

    with transaction.atomic():
        probe = Attempt(
            time=timezone.now(), task=task, game=game,
            team=team, user=user, anon_key=anon_key,
        )
        mode = game.get_current_mode(probe)
        ChainTaskState.objects.get_or_create(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=mode,
            defaults={'state': None},
        )
        row = ChainTaskState.objects.select_for_update().get(
            team=team, user=user, anon_key=anon_key,
            task=task, game=game, game_mode=mode,
        )
        grid, required, rares = parse_task_payload(task.checker_data, task.answer or '')
        last_state = row.state
        state = load_state(last_state)
        existing = _word_salad_attempts(
            task, team=team, user=user, anon_key=anon_key, game=game,
        )
        seen_texts = {attempt.text for attempt in existing}
        stamp = next(
            (
                attempt.time
                for attempt in reversed(existing)
                if not attempt.skip and attempt.time is not None
            ),
            None,
        )
        checker_type = task.get_checker()
        checker_data = task.checker_data or ''

        def known_words():
            rare_ui, extra_ui = classify_side_finds(state, required, rares)
            names = {item['normalized'] for item in rare_ui}
            names.update(extra_ui)
            solved = set(state.get('solved_indices') or [])
            for index, required_word in enumerate(required):
                if index in solved:
                    names.add(normalize_word(required_word))
            return names

        for word in words_in:
            if word in known_words():
                continue
            persisted = False
            for path in find_paths(grid, word, active=range(16), limit=8):
                text = json.dumps({'action': 'solve', 'path': path}, ensure_ascii=False)
                if text in seen_texts:
                    continue
                attempt = Attempt(
                    team=team, user=user, anon_key=anon_key,
                    task=task, game=game, text=text,
                    status='Pending', points=0,
                )
                attempt.task_revision = task.attempt_revision
                result = _check_word_salad_attempt(
                    checker_type, checker_data, last_state, attempt,
                    expand_active=True,
                )
                if result.status not in ('Ok', 'Partial'):
                    continue
                _apply_word_salad_check_result(attempt, result)
                attempt.save()
                if stamp is not None:
                    Attempt.manager.filter(pk=attempt.pk).update(time=stamp)
                    attempt.time = stamp
                seen_texts.add(text)
                last_state = attempt.state
                state = load_state(last_state)
                row.state = last_state
                row.last_attempt = attempt
                comment = attempt.comment or ''
                if comment == RARE_FOUND_COMMENT:
                    credited['rare'].append(word)
                elif comment == EXTRA_FOUND_COMMENT:
                    credited['extra'].append(word)
                else:
                    credited['answer'].append(word)
                persisted = True
                break
            if not persisted:
                continue
        row.save(update_fields=['state', 'last_attempt', 'updated_at'])
    return {'credited': credited, 'state': last_state}
