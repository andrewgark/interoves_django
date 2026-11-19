"""Shared Замены drafts: POST /send_replacements_ui/<task_id>/."""

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.gameplay.moves import REPLAY_REQUIRED, MoveContext, official_game_completed
from games.models import Attempt, ReplacementsUiState
from games.puzzles.replacements.lines import (
    parse_replacements_lines_text,
    replacements_solved_slots_from_state,
)
from games.puzzles.replacements.lines.ui_state import (
    apply_guess_changes,
    parse_changes,
    replacements_ui_payload,
    seed_from_other_mode,
    task_revision,
    ui_state_lookup,
)
from games.realtime.db import (
    lock_or_create_state_row,
    run_atomic_with_lock_retry,
)
from games.realtime.track_compat import track_actor_task_change
from games.site.http.exceptions import NoGameAccessException
from games.site.http.request_util import (
    get_public_task_or_404,
    is_browser_form_submission,
    redirect_after_browser_submission,
)
from games.site.session.gameplay_context import context_error_response
from games.site.views.game.context import resolve_move_context, submitter_profile
from games.task_group.replacements_state import current_state


def process_send_replacements_ui(request, task_id):
    """Merge draft changes slot by slot and push the result to teammates."""
    task = get_public_task_or_404(task_id)
    if task.task_type != 'replacements_lines':
        return {'status': 'invalid'}
    ctx = resolve_move_context(request, task)
    if not isinstance(ctx, MoveContext):
        return ctx
    if ctx.replay_slot is None and official_game_completed(ctx):
        return dict(REPLAY_REQUIRED)
    changes = parse_changes(request.POST.get('changes'))
    if changes is None:
        return {'status': 'invalid_form'}
    if not changes:
        return {'status': 'empty'}
    team, user, anon_key, game, replay_slot = ctx.team, ctx.user, ctx.anon_key, ctx.game, ctx.replay_slot

    parsed = parse_replacements_lines_text(task.text, (task.checker_data or '').strip() or None)
    slot_counts = [len(row) for row in parsed['answers']]
    current_mode = game.get_current_mode(Attempt(time=timezone.now()))
    # Solved slots are read without a lock: a check racing with this save can
    # leave one stale draft on a just-solved slot, which every reader ignores.
    solved = replacements_solved_slots_from_state(
        current_state(
            task, game=game, team=team, user=user, anon_key=anon_key,
            mode=current_mode, replay_slot=replay_slot,
        ),
        parsed,
    )
    lookup = ui_state_lookup(
        task, game=game, mode=current_mode, team=team, user=user,
        anon_key=anon_key, replay_slot=replay_slot,
    )
    revision_now = task_revision(task)
    saved = {}

    def _save_ui_state():
        row = lock_or_create_state_row(
            queryset=ReplacementsUiState.objects.filter(**lookup),
            task=task,
            lookup=lookup,
            # Drafts survive a mode switch (tournament -> general).
            defaults=lambda: seed_from_other_mode(lookup, task),
        )
        base = row.guesses if row.task_revision == revision_now else {}
        row.guesses = apply_guess_changes(base, changes, slot_counts, solved)
        row.task_revision = revision_now
        row.revision = int(row.revision or 0) + 1
        row.updated_by_profile = submitter_profile(request)
        row.save(update_fields=[
            'guesses', 'task_revision', 'revision', 'updated_by_profile', 'updated_at',
        ])
        saved['guesses'] = row.guesses
        saved['revision'] = row.revision

    run_atomic_with_lock_retry(_save_ui_state, label='replacements_ui')

    payload = replacements_ui_payload(task.id, saved['guesses'], saved['revision'])
    if replay_slot is None:
        track_actor_task_change(
            task,
            team=team,
            update_html=payload,
            game=game,
            user=user,
            anon_key=anon_key,
            current_mode=current_mode,
            reason='replacements.ui_state',
        )
    result = {'status': 'ok', 'task_id': task.id}
    result.update(payload)
    return result


@require_POST
def send_replacements_ui(request, task_id):
    try:
        response = process_send_replacements_ui(request, task_id)
    except NoGameAccessException:
        response = {'status': 'no_access'}
    context_response = context_error_response(response)
    if context_response is not None:
        if is_browser_form_submission(request):
            return redirect_after_browser_submission(request)
        return context_response
    if is_browser_form_submission(request):
        return redirect_after_browser_submission(request)
    return JsonResponse(response)
