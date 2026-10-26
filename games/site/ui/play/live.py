"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


from django.utils import timezone



from games.daily_section import (
    scheduled_number_is_public,
)
from games.models import (
    Attempt,
    Game,
    GameTaskGroup,
    Task,
)
from games.replay import active_replay
from games.site.views.pages.util import (
    effective_play_mode,
    has_profile,
    has_team,
    personal_play_mode_locked,
)


from games.site.ui import (
    NEW_UI_SECTIONS_PROJECT,
    _anon_key_from_request,
    _get_play_mode,
    _root_project_game_redirect,
)

logger = logging.getLogger('games.views.new_ui')

@never_cache
@require_http_methods(['GET'])
def new_task_group_live_state(request, game_id, project_id=None):
    """Authoritative task-card projection used after socket gaps and reconnects."""
    if project_id:
        game = get_object_or_404(Game, id=game_id, project_id=project_id)
    else:
        game = get_object_or_404(Game, id=game_id)
        project_redirect = _root_project_game_redirect(request, game, 'live-state/')
        if project_redirect is not None:
            return project_redirect
    requested_mode = request.GET.get('play_mode')
    if requested_mode not in ('team', 'personal'):
        requested_mode = None
    play_mode = requested_mode or _get_play_mode(request, game.project_id)[0]
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = user = anon_key = None

    if request.user.is_authenticated:
        if play_mode == 'team':
            if not has_team(request.user):
                raise Http404()
            team = request.user.profile.team_on
        else:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
    else:
        if personal_play_mode_locked(game, user=request.user):
            raise Http404()
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
        if not anon_key:
            raise Http404()

    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        preview_team = request.user.profile.team_on if has_profile(request.user) else None
        if not game.has_access('see_game_preview', team=preview_team):
            raise Http404()
    elif play_mode == 'team':
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        now_attempt = Attempt(time=timezone.now())
        if (
            not game.has_access('read_googledoc', team=None, attempt=now_attempt)
            or not game.is_playable
        ):
            raise Http404()

    raw_ids = (request.GET.get('task_ids') or '').strip()
    try:
        task_ids = list(dict.fromkeys(int(value) for value in raw_ids.split(',') if value.strip()))
    except (TypeError, ValueError):
        return JsonResponse({'status': 'invalid_task_ids'}, status=400)
    if not task_ids or len(task_ids) > 100:
        return JsonResponse({'status': 'invalid_task_ids'}, status=400)

    tasks = list(
        Task.objects.visible()
        .select_related('task_group')
        .filter(pk__in=task_ids)
    )
    if len(tasks) != len(task_ids):
        raise Http404()
    tasks_by_id = {task.pk: task for task in tasks}
    tasks = [tasks_by_id[task_id] for task_id in task_ids]

    task_group = tasks[0].task_group
    replay_slot = active_replay(
        request=request,
        game=game,
        task_group=task_group,
        team=team,
        user=user,
        anon_key=anon_key,
    )

    placements = list(
        GameTaskGroup.objects.filter(
            game=game,
            task_group_id__in={task.task_group_id for task in tasks},
        ).select_related('task_group')
    )
    placements_by_group = {placement.task_group_id: placement for placement in placements}
    if any(task.task_group_id not in placements_by_group for task in tasks):
        raise Http404()

    if not request.user.is_staff:
        from games.club_access import reject_if_club_archive_blocked
        from games.site.views.game.context import unpublished_scheduled_task_response

        for placement in placements:
            if placement.is_deferred:
                raise Http404()
            if not scheduled_number_is_public(game, placement.number):
                task = next(
                    (item for item in tasks if item.task_group_id == placement.task_group_id),
                    None,
                )
                if task is None or unpublished_scheduled_task_response(request, game, task) is not None:
                    raise Http404()
            locked = reject_if_club_archive_blocked(
                request, game, number=placement.number, json_mode=True,
            )
            if locked is not None:
                return locked

    from games.site.views.game.render import render_new_ui_task_card_html
    from games.realtime.track_compat import current_track_versions

    # Sample revisions before rendering. A mutation racing with this request may
    # then produce a newer queued socket event, but an older projection can never
    # claim that newer revision and suppress the event on the client.
    versions = current_track_versions(
        game.id,
        user_id=request.user.id if request.user.is_authenticated else None,
        team_id=team.pk if team is not None else None,
    )
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    fragments = {}
    for task in tasks:
        fragment = render_new_ui_task_card_html(
            request,
            task,
            team,
            mode,
            user=user,
            anon_key=anon_key,
            game=game,
            replay_slot=replay_slot,
        )
        if fragment:
            fragments[str(task.pk)] = fragment

    return JsonResponse({
        'status': 'ok',
        'update_task_html_new': fragments,
        'versions': versions,
        'reload_required': len(fragments) != len(tasks),
    })



__all__ = [
    'new_task_group_live_state',
]
