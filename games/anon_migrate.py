"""Перенос анонимного прогресса на залогиненного пользователя."""
import json

from games.models import ChainTaskState


def _solved_count(state_json):
    if not state_json:
        return -1
    try:
        data = json.loads(state_json)
    except (TypeError, ValueError):
        return -1
    solved = data.get('solved_indices')
    if isinstance(solved, list):
        return len(solved)
    return -1


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
        # Конфликт: берём state с большим числом solved_indices (raddle/wall).
        if _solved_count(row.state) > _solved_count(existing.state):
            existing.state = row.state
            existing.last_attempt = row.last_attempt
            existing.save(update_fields=['state', 'last_attempt', 'updated_at'])
        row.delete()
        moved += 1
    return moved


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
