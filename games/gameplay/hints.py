"""Hint requests of a gameplay actor."""

from django.db import transaction
from django.utils import timezone

from games.models import Attempt, GameTaskGroup, Hint, HintAttempt, Task
from games.site.http.exceptions import DuplicateAttemptException, NotAllRequiredHintsTakenException


def _hintattempt_filter(team=None, user=None, anon_key=None):
    if team is not None:
        return {'team': team, 'user__isnull': True, 'anon_key__isnull': True}
    if user is not None:
        return {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
    return {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}


def create_hint_attempt(
    hint, team=None, user=None, anon_key=None, game=None, replay_slot=None,
    requested_by_profile=None,
):
    task = hint.task
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        raise Exception('Cannot resolve game for hint (pass game= or single-linked task group)')

    with transaction.atomic():
        # Serialize all hint requests for this task.  The previous check-then-save
        # allowed two simultaneous requests to create duplicate rows.
        Task.objects.select_for_update().only('pk').get(pk=task.pk)
        actor_filter = _hintattempt_filter(team=team, user=user, anon_key=anon_key)
        if HintAttempt.objects.filter(hint=hint, replay_slot=replay_slot, **actor_filter).exists():
            raise DuplicateAttemptException('Вы уже запрашивали эту подсказку')

        required_hints = set(hint.required_hints.all())
        taken_required = HintAttempt.objects.filter(
            hint__in=required_hints, replay_slot=replay_slot, **actor_filter
        ).values('hint_id').distinct().count()
        if taken_required < len(required_hints):
            raise NotAllRequiredHintsTakenException('Вы не можете пока взять эту подсказку')

        hint_attempt = HintAttempt(
            team=team,
            user=user,
            requested_by_profile=requested_by_profile,
            anon_key=anon_key,
            hint=hint,
            replay_slot=replay_slot,
            time=timezone.now(),
        )
        current_mode = game.get_current_mode(hint_attempt)
        attempts_info = Attempt.manager.get_attempts_info(
            team=team, user=user, anon_key=anon_key, task=task, mode=current_mode, game=game,
            replay_slot=replay_slot,
        )
        hint_attempt.is_real_request = not attempts_info.is_solved()
        hint_attempt.save()
    return hint_attempt, current_mode


def get_first_new_hint_actor(task, team=None, user=None, anon_key=None, replay_slot=None):
    hints = Hint.objects.filter(task=task)
    hints = sorted(hints, key=lambda h: h.key_sort())
    for hint in hints:
        if team is not None:
            exists = HintAttempt.objects.filter(
                team=team, user__isnull=True, anon_key__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        elif user is not None:
            exists = HintAttempt.objects.filter(
                user=user, team__isnull=True, anon_key__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        else:
            exists = HintAttempt.objects.filter(
                anon_key=anon_key, team__isnull=True, user__isnull=True,
                hint=hint, replay_slot=replay_slot,
            ).exists()
        if not exists:
            return hint
    return None
