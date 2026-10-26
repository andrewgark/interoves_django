"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from games.daily.catalog.alphabetty import ALPHABETTY_GAME_ID
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.daily.page.section import WORD_SALAD_GAME_ID
from games.identity import has_profile
from games.models import (
    Attempt,
    BugReport,
    Game,
    Like,
    Task,
)
from games.ops.analytics.identity import (
    gameplay_anon_key,
)
from games.site.http.request_util import get_public_task_or_404
from games.site.pages import _get_play_mode
from games.site.session.play_mode import effective_play_mode
from games.site.views.game.context import game_from_request_for_task

logger = logging.getLogger('games.views.new_ui')

def _reaction_task_and_actor(request, task_id):
    """Task and actor (team, user, anon_key) for a like; Http404 when not allowed."""
    game_hint = (
        (request.POST.get('game_id') or request.GET.get('game_id') or '').strip()
        or (request.headers.get('X-Interoves-Game') or '').strip()
    )
    try:
        task = get_public_task_or_404(task_id)
    except Http404:
        task = Task.objects.filter(pk=task_id).select_related('task_group').first()
        if task is None:
            raise
        if game_hint == ALPHABETTY_GAME_ID:
            from games.models import AlphabettyOffer
            if not AlphabettyOffer.objects.filter(task_group_id=task.task_group_id).exists():
                raise Http404()
        elif game_hint == LADDER_GAME_ID:
            from games.models import LadderOffer
            if not LadderOffer.objects.filter(task_group_id=task.task_group_id).exists():
                raise Http404()
        elif game_hint == WORD_SALAD_GAME_ID:
            from games.models import WordSaladOffer
            if not WordSaladOffer.objects.filter(task_group_id=task.task_group_id).exists():
                raise Http404()
        else:
            raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        if game_hint in (ALPHABETTY_GAME_ID, LADDER_GAME_ID, WORD_SALAD_GAME_ID):
            game = Game.objects.filter(id=game_hint).first()
        if game is None:
            raise Http404()
    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            user = request.user
        else:
            anon_key = gameplay_anon_key(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()
    return task, team, user, anon_key


def _posted_reaction(request):
    """-1/0/1 to store, None for no change, or JsonResponse on bad input."""
    try:
        likes = int(request.POST.get('likes', 0) or 0)
        dislikes = int(request.POST.get('dislikes', 0) or 0)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Некорректная реакция.'}, status=400)
    # (-1, 1) / (1, -1) — переключение: старый jQuery-плагин шлёт снятие и новую реакцию разом.
    if likes not in (-1, 0, 1) or dislikes not in (-1, 0, 1) or (likes == 1 and dislikes == 1):
        return JsonResponse({'error': 'Некорректная реакция.'}, status=400)
    if likes == 1:
        return 1
    if dislikes == 1:
        return -1
    if likes == -1 or dislikes == -1:
        return 0
    return None


@require_http_methods(['POST'])
def like_dislike(request, task_id):
    task, team, user, anon_key = _reaction_task_and_actor(request, task_id)
    reaction = _posted_reaction(request)
    if isinstance(reaction, JsonResponse):
        return reaction
    if reaction is not None:
        Like.manager.set_actor_reaction(
            task, reaction, team=team, user=user, anon_key=anon_key,
        )

    # Суммы — командные + личные лайки; liked/disliked — текущего режима.
    from games.task_group.likes_meta import likes_meta_by_task_id
    return JsonResponse(
        likes_meta_by_task_id([task], team=team, user=user, anon_key=anon_key)[task.id]
    )


@require_http_methods(['POST'])
def raddle_clue_like(request, task_id, hint_index):
    from games.puzzles.raddle.clue_likes import clue_reactions_meta, set_clue_reaction
    from games.puzzles.raddle.engine import parse_raddle_data

    task, team, user, anon_key = _reaction_task_and_actor(request, task_id)
    parsed = parse_raddle_data(task)
    if not parsed or not 0 <= hint_index < len(parsed['hints']):
        raise Http404()
    reaction = _posted_reaction(request)
    if isinstance(reaction, JsonResponse):
        return reaction
    if reaction is not None:
        set_clue_reaction(
            task, hint_index, reaction, str(parsed['hints'][hint_index]),
            team=team, user=user, anon_key=anon_key,
        )
    meta = clue_reactions_meta(task, team=team, user=user, anon_key=anon_key)
    return JsonResponse(meta.get(hint_index) or {
        'likes': 0, 'dislikes': 0, 'liked': False, 'disliked': False,
    })


@require_http_methods(['POST'])
def bug_report(request, task_id):
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()
    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = request.user if request.user.is_authenticated else None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('send_attempt', team=team):
            raise Http404()
    else:
        if user is None:
            anon_key = gameplay_anon_key(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'ok': False, 'error': 'Опишите проблему.'}, status=400)
    if len(text) > 5000:
        return JsonResponse({'ok': False, 'error': 'Слишком длинное сообщение.'}, status=400)

    from games.ops.telegram.links import task_play_url
    from games.site.pages.profile.feedback import profile_reports_path

    report = BugReport.objects.create(
        task=task,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        text=text,
        page_url=task_play_url(game, task),
        status='Pending',
    )
    return JsonResponse({
        'ok': True,
        'report_id': report.pk,
        'report_url': profile_reports_path(project_id=game.project_id, report_id=report.pk),
    })


def _game_from_next_path(path):
    """Если next ведёт на страницу игры — вернуть Game или None."""
    if not path:
        return None
    from urllib.parse import urlparse

    from django.urls import Resolver404, resolve
    try:
        match = resolve(urlparse(path).path)
    except Resolver404:
        return None
    game_id = match.kwargs.get('game_id')
    if not game_id:
        return None
    return Game.objects.filter(pk=game_id).first()


__all__ = [
    'like_dislike',
    'raddle_clue_like',
    'bug_report',
    '_game_from_next_path',
]

