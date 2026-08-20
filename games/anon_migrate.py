"""Перенос анонимного прогресса на залогиненного пользователя."""
import json

from django.db import transaction

from games.alphabetty.core import normalize_word
from games.models import (
    AlphabettyDictSuggestion,
    AlphabettyPersonalDictWord,
    Attempt,
    BugReport,
    ChainTaskState,
    HintAttempt,
    Like,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
)


def anon_migration_counts(anon_key):
    """Return all guest-owned data that has user-visible or analytic value."""
    if not anon_key:
        return {}
    return {
        'attempts': Attempt.manager.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'hints': HintAttempt.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'states': ChainTaskState.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'likes': Like.manager.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'personal_dict': AlphabettyPersonalDictWord.objects.filter(
            anon_key=anon_key, user__isnull=True,
        ).count(),
        'started_games': PlayerStartedGame.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'completed_games': PlayerCompletedGame.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'analytics_states': PlayerAnalyticsState.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'bug_reports': BugReport.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).count(),
        'dict_suggestions': AlphabettyDictSuggestion.objects.filter(
            anon_key=anon_key, user__isnull=True,
        ).count(),
    }


def migrate_anon_attributions(user, anon_key):
    """Attach guest reports/suggestions that do not need conflict merging."""
    if not user or not anon_key:
        return {'bug_reports': 0, 'dict_suggestions': 0}
    return {
        'bug_reports': BugReport.objects.filter(
            anon_key=anon_key, user__isnull=True, team__isnull=True,
        ).update(user=user, anon_key=None),
        'dict_suggestions': AlphabettyDictSuggestion.objects.filter(
            anon_key=anon_key, user__isnull=True,
        ).update(user=user, anon_key=None),
    }


def _parse_state(state_json):
    if not state_json:
        return None
    try:
        data = json.loads(state_json)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _solved_count(state_json):
    """Эвристика «насколько продвинут state» для merge anon→user.

    Поддерживает raddle (solved_indices), replacements (solved_lines),
    alphabetty (guesses/won) и wall (best_points).
    """
    data = _parse_state(state_json)
    if data is None:
        return -1
    solved = data.get('solved_indices')
    if isinstance(solved, list):
        return len(solved)
    solved_lines = data.get('solved_lines')
    if isinstance(solved_lines, list):
        return len(solved_lines)
    guesses = data.get('guesses')
    if isinstance(guesses, list):
        n = len(guesses)
        # Победа важнее любого незавершённого прогресса; среди побед — меньше попыток.
        if data.get('won'):
            return 10_000 - n
        return n
    best = data.get('best_points')
    if best is not None:
        try:
            return int(float(best))
        except (TypeError, ValueError):
            pass
    return -1


def _merge_alphabetty_states(anon_json, user_json):
    """Объединяет guesses Алфавитки; won=True если победил хоть один side."""
    anon = _parse_state(anon_json) or {}
    user = _parse_state(user_json) or {}
    anon_g = anon.get('guesses') if isinstance(anon.get('guesses'), list) else None
    user_g = user.get('guesses') if isinstance(user.get('guesses'), list) else None
    if anon_g is None or user_g is None:
        return None
    # Порядок: сначала более «продвинутый» state, затем недостающие из другого.
    if _solved_count(anon_json) >= _solved_count(user_json):
        primary, secondary = anon_g, user_g
    else:
        primary, secondary = user_g, anon_g
    seen = set()
    merged = []
    for g in list(primary) + list(secondary):
        if not isinstance(g, str) or g in seen:
            continue
        seen.add(g)
        merged.append(g)
    anon_hp = str(anon.get('hint_prefix') or '').strip().upper().replace('Ё', 'Е')
    user_hp = str(user.get('hint_prefix') or '').strip().upper().replace('Ё', 'Е')
    try:
        hints_taken = max(int(anon.get('hints_taken') or 0), int(user.get('hints_taken') or 0))
    except (TypeError, ValueError):
        hints_taken = max(len(anon_hp), len(user_hp))
    return json.dumps({
        'guesses': merged,
        'won': bool(anon.get('won') or user.get('won')),
        'hint_prefix': anon_hp if len(anon_hp) >= len(user_hp) else user_hp,
        'hints_taken': hints_taken,
    }, ensure_ascii=False)


def _rebuild_alphabetty_state_from_attempts(*, user, task, game, anon_json, user_json):
    """
    Восстановить состояние Алфавитки из реальных Attempt после переноса anon→user.

    JSON-merge двух независимых прохождений даёт невозможный порядок guesses
    (например, победное слово оказывается в середине, а ранние слова — после него).
    Для alphabetty порядок guesses семантически важен: он влияет на share и UI.
    """
    attempts = (
        Attempt.manager.filter(
            user=user,
            team__isnull=True,
            anon_key__isnull=True,
            task=task,
            game=game,
        )
        .exclude(time__isnull=True)
        .order_by('time', 'id')
    )
    guesses = []
    seen = set()
    won = False
    for attempt in attempts:
        word = normalize_word(attempt.text or '')
        if not word or word in seen:
            if attempt.status == 'Ok':
                won = True
            continue
        seen.add(word)
        guesses.append(word)
        if attempt.status == 'Ok':
            won = True

    if not guesses:
        return None

    anon = _parse_state(anon_json) or {}
    user_state = _parse_state(user_json) or {}
    anon_hp = str(anon.get('hint_prefix') or '').strip().upper().replace('Ё', 'Е')
    user_hp = str(user_state.get('hint_prefix') or '').strip().upper().replace('Ё', 'Е')
    try:
        hints_taken = max(int(anon.get('hints_taken') or 0), int(user_state.get('hints_taken') or 0))
    except (TypeError, ValueError):
        hints_taken = max(len(anon_hp), len(user_hp))

    return json.dumps({
        'guesses': guesses,
        'won': bool(won or anon.get('won') or user_state.get('won')),
        'hint_prefix': anon_hp if len(anon_hp) >= len(user_hp) else user_hp,
        'hints_taken': hints_taken,
    }, ensure_ascii=False)


def migrate_anon_personal_dict_words(user, anon_key):
    """Переносит AlphabettyPersonalDictWord с anon_key на user. Возвращает число строк."""
    if not user or not anon_key:
        return 0
    moved = 0
    qs = AlphabettyPersonalDictWord.objects.filter(
        anon_key=anon_key, user__isnull=True,
    )
    for row in qs.iterator():
        if AlphabettyPersonalDictWord.objects.filter(user=user, word=row.word).exists():
            row.delete()
            moved += 1
            continue
        row.user = user
        row.anon_key = None
        row.save(update_fields=['user', 'anon_key'])
        moved += 1
    return moved


@transaction.atomic
def migrate_anon_likes(user, anon_key):
    """Переносит реакции anon→user, оставляя одну реакцию на задание.

    Если профиль уже успел поставить реакцию на то же задание, она считается
    более актуальной и анонимная реакция удаляется. Это одновременно убирает
    задвоение счётчика после повторного клика уже под авторизацией.

    Returns:
        int: сколько анонимных строк обработано (перенесено или схлопнуто).
    """
    if not user or not anon_key:
        return 0

    anon_rows = list(
        Like.manager.select_for_update()
        .filter(anon_key=anon_key, user__isnull=True, team__isnull=True)
        .order_by('task_id', '-id')
    )
    if not anon_rows:
        return 0

    task_ids = {row.task_id for row in anon_rows}
    user_qs = Like.manager.select_for_update().filter(
        user=user, team__isnull=True, anon_key__isnull=True,
    )
    non_null_task_ids = {task_id for task_id in task_ids if task_id is not None}
    user_rows = list(user_qs.filter(task_id__in=non_null_task_ids))
    if None in task_ids:
        user_rows.extend(user_qs.filter(task__isnull=True))

    anon_by_task = {}
    for row in anon_rows:
        anon_by_task.setdefault(row.task_id, []).append(row)
    user_by_task = {}
    for row in user_rows:
        user_by_task.setdefault(row.task_id, []).append(row)

    for task_id, rows in anon_by_task.items():
        existing = user_by_task.get(task_id) or []
        if existing:
            # Авторизованная реакция побеждает. Заодно нормализуем возможные
            # старые дубликаты профиля, оставляя самую новую строку.
            keep = max(existing, key=lambda row: row.id)
            duplicate_ids = [row.id for row in existing if row.id != keep.id]
            Like.manager.filter(id__in=duplicate_ids).delete()
            Like.manager.filter(id__in=[row.id for row in rows]).delete()
            continue

        # В норме анонимная строка одна. Если старые гонки оставили несколько,
        # последняя по id лучше всего отражает итоговый клик пользователя.
        keep = max(rows, key=lambda row: row.id)
        duplicate_ids = [row.id for row in rows if row.id != keep.id]
        Like.manager.filter(id__in=duplicate_ids).delete()
        keep.user = user
        keep.anon_key = None
        keep.save(update_fields=['user', 'anon_key'])

    return len(anon_rows)


def migrate_anon_chain_task_states(user, anon_key):
    """
    Переносит ChainTaskState с anon_key на user.

    Attempt/HintAttempt уже могут быть перенесены отдельно; без этого шага
    UI (из Attempt.state) показывает прогресс, а чекер (из ChainTaskState)
    стартует с нуля — «крайние слова» отклоняют верные ответы на открытых ступеньках.

    Returns:
        int: сколько строк обработано (перенесено или смержено).
    """
    if not user or not anon_key:
        return 0
    moved = 0
    qs = ChainTaskState.objects.filter(
        anon_key=anon_key, user__isnull=True, team__isnull=True,
    )
    for row in qs.iterator():
        existing = ChainTaskState.objects.filter(
            user=user,
            team__isnull=True,
            anon_key__isnull=True,
            task_id=row.task_id,
            game_id=row.game_id,
            game_mode=row.game_mode,
        ).first()
        if existing is None:
            row.user = user
            row.anon_key = None
            row.save(update_fields=['user', 'anon_key', 'updated_at'])
            moved += 1
            continue
        merged = None
        if getattr(row.task, 'task_type', None) == 'alphabetty':
            merged = _rebuild_alphabetty_state_from_attempts(
                user=user,
                task=row.task,
                game=row.game,
                anon_json=row.state,
                user_json=existing.state,
            )
        if merged is None:
            merged = _merge_alphabetty_states(row.state, existing.state)
        if merged is not None:
            if _solved_count(row.state) > _solved_count(existing.state):
                existing.last_attempt = row.last_attempt
            existing.state = merged
            existing.save(update_fields=['state', 'last_attempt', 'updated_at'])
        elif _solved_count(row.state) > _solved_count(existing.state):
            existing.state = row.state
            existing.last_attempt = row.last_attempt
            existing.save(update_fields=['state', 'last_attempt', 'updated_at'])
        row.delete()
        moved += 1
    return moved


@transaction.atomic
def migrate_anon_started_games(user, anon_key):
    """Merge unique server-side game starts from an anonymous actor into a user."""
    if not user or not anon_key:
        return 0
    moved = 0
    rows = list(
        PlayerStartedGame.objects.select_for_update().filter(
            anon_key=anon_key,
            user__isnull=True,
            team__isnull=True,
        )
    )
    for row in rows:
        existing = PlayerStartedGame.objects.select_for_update().filter(
            user=user,
            team__isnull=True,
            anon_key__isnull=True,
            game_instance_id=row.game_instance_id,
        ).first()
        if existing is None:
            row.user = user
            row.anon_key = None
            row.save(update_fields=['user', 'anon_key'])
            moved += 1
            continue
        updates = []
        if row.started_at and (not existing.started_at or row.started_at < existing.started_at):
            existing.started_at = row.started_at
            updates.append('started_at')
        if existing.metrika_acked_at is None and row.metrika_acked_at is not None:
            existing.metrika_acked_at = row.metrika_acked_at
            updates.append('metrika_acked_at')
        merged_backfilled = bool(existing.is_backfilled and row.is_backfilled)
        if existing.is_backfilled != merged_backfilled:
            existing.is_backfilled = merged_backfilled
            updates.append('is_backfilled')
        if updates:
            existing.save(update_fields=updates)
        row.delete()
        moved += 1
    return moved


@transaction.atomic
def migrate_anon_completed_games(user, anon_key):
    """Merge completion delivery state into the authenticated analytics actor."""
    if not user or not anon_key:
        return 0
    moved = 0
    rows = list(
        PlayerCompletedGame.objects.select_for_update().filter(
            anon_key=anon_key,
            user__isnull=True,
            team__isnull=True,
        )
    )
    for row in rows:
        existing = PlayerCompletedGame.objects.select_for_update().filter(
            user=user,
            team__isnull=True,
            anon_key__isnull=True,
            game_instance_id=row.game_instance_id,
        ).first()
        if existing is None:
            row.user = user
            row.anon_key = None
            row.save(update_fields=['user', 'anon_key'])
            moved += 1
            continue
        updates = []
        if row.completed_at and row.completed_at < existing.completed_at:
            existing.completed_at = row.completed_at
            updates.append('completed_at')
        if existing.metrika_acked_at is None and row.metrika_acked_at is not None:
            existing.metrika_acked_at = row.metrika_acked_at
            updates.append('metrika_acked_at')
        merged_backfilled = bool(existing.is_backfilled and row.is_backfilled)
        if existing.is_backfilled != merged_backfilled:
            existing.is_backfilled = merged_backfilled
            updates.append('is_backfilled')
        if updates:
            existing.save(update_fields=updates)
        row.delete()
        moved += 1
    return moved


@transaction.atomic
def migrate_anon_analytics_state(user, anon_key):
    """Merge the anonymous activation marker into the authenticated actor."""
    if not user or not anon_key:
        return 0
    row = PlayerAnalyticsState.objects.select_for_update().filter(
        anon_key=anon_key,
        user__isnull=True,
        team__isnull=True,
    ).first()
    if row is None:
        return 0
    existing = PlayerAnalyticsState.objects.select_for_update().filter(
        user=user,
        team__isnull=True,
        anon_key__isnull=True,
    ).first()
    if existing is None:
        row.user = user
        row.anon_key = None
        row.save(update_fields=['user', 'anon_key', 'updated_at'])
        return 1
    updates = []
    activation_backfill_sources = []
    if existing.activated_at is not None:
        activation_backfill_sources.append(existing.activation_is_backfilled)
    if row.activated_at is not None:
        activation_backfill_sources.append(row.activation_is_backfilled)
    if row.activated_at and (not existing.activated_at or row.activated_at < existing.activated_at):
        existing.activated_at = row.activated_at
        updates.append('activated_at')
    if existing.activation_goal_acked_at is None and row.activation_goal_acked_at is not None:
        existing.activation_goal_acked_at = row.activation_goal_acked_at
        updates.append('activation_goal_acked_at')
    merged_backfilled = bool(
        activation_backfill_sources and all(activation_backfill_sources)
    )
    if existing.activation_is_backfilled != merged_backfilled:
        existing.activation_is_backfilled = merged_backfilled
        updates.append('activation_is_backfilled')
    if updates:
        existing.save(update_fields=updates + ['updated_at'])
    row.delete()
    return 1


def heal_orphaned_chain_states_from_migrate_events():
    """
    Для всех прошлых anon_attempts_migrated: догнать ChainTaskState,
    оставшиеся на anon_key. Возвращает (events, rows_moved).
    """
    from games.models import StatisticsEvent
    from django.contrib.auth import get_user_model

    User = get_user_model()
    events = 0
    rows = 0
    for ev in StatisticsEvent.objects.filter(
        kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
    ).iterator():
        payload = ev.payload or {}
        anon_key = payload.get('anon_key')
        if not anon_key or not ev.user_id:
            continue
        user = User.objects.filter(pk=ev.user_id).first()
        if user is None:
            continue
        n = migrate_anon_chain_task_states(user, anon_key)
        if n:
            events += 1
            rows += n
    return events, rows


def heal_orphaned_likes_from_migrate_events():
    """Догоняет реакции для уже состоявшихся anon→user восстановлений."""
    from games.models import StatisticsEvent
    from django.contrib.auth import get_user_model

    User = get_user_model()
    events = 0
    rows = 0
    for ev in StatisticsEvent.objects.filter(
        kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
    ).order_by('id').iterator():
        payload = ev.payload or {}
        anon_key = payload.get('anon_key')
        if not anon_key or not ev.user_id:
            continue
        user = User.objects.filter(pk=ev.user_id).first()
        if user is None:
            continue
        n = migrate_anon_likes(user, anon_key)
        if n:
            events += 1
            rows += n
    return events, rows
