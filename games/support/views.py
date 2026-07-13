from django.contrib.auth.views import LoginView
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from games.models import Game, Team
from games.support.access import support_console_required
from games.support.services.actor import (
    build_anon_context,
    build_game_context,
    build_team_context,
    build_user_context,
)
from games.support.services.chain import build_chain_context, format_chain_state
from games.support.services.games import get_all_games_by_project
from games.support.services.live import get_live_feed
from games.support.services.preview import (
    ActorSpec,
    build_preview_game_context,
    build_preview_task_group_context,
    parse_actor_spec,
)
from games.support.services.pending import get_pending_queue
from games.support.services.search import search
from games.support.services.sections import get_sections_dashboard
from games.support.services.stats import collect_support_stats
from games.support.services.stuck import get_stuck_teams


class SupportLoginView(LoginView):
    template_name = 'support/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse('support:hub')


def _feed_kwargs_from_request(request):
    kind = request.GET.get('kind', 'all')
    if kind not in ('all', 'attempts', 'hints'):
        kind = 'all'
    status = request.GET.get('status') or None
    if status and status not in ('Ok', 'Pending', 'Partial', 'Wrong'):
        status = None
    hours_raw = request.GET.get('hours')
    hours = None
    if hours_raw:
        try:
            hours = int(hours_raw)
        except (TypeError, ValueError):
            hours = None
    per_page = 50
    per_page_raw = request.GET.get('per_page')
    if per_page_raw:
        try:
            per_page = min(200, max(10, int(per_page_raw)))
        except (TypeError, ValueError):
            pass
    page = 1
    page_raw = request.GET.get('page')
    if page_raw:
        try:
            page = max(1, int(page_raw))
        except (TypeError, ValueError):
            pass
    return {
        'kind': kind,
        'status': status,
        'hours': hours,
        'page': page,
        'per_page': per_page,
    }


def _feed_filters(request):
    return {
        'kind': request.GET.get('kind', 'all'),
        'status': request.GET.get('status', ''),
        'hours': request.GET.get('hours', ''),
        'page': request.GET.get('page', ''),
        'per_page': request.GET.get('per_page', ''),
    }


@support_console_required
def hub(request):
    query = (request.GET.get('q') or '').strip()
    results = search(query) if query else []
    return render(request, 'support/hub.html', {
        'page_title': 'Support',
        'query': query,
        'results': results,
    })


@support_console_required
def games_browse(request):
    project_id = (request.GET.get('project') or '').strip() or None
    return render(request, 'support/games.html', {
        'page_title': 'Игры',
        'game_groups': get_all_games_by_project(project_id=project_id),
        'project_id': project_id or '',
    })


@support_console_required
def sections_dashboard(request):
    return render(request, 'support/sections.html', {
        'page_title': 'Разделы',
        'sections': get_sections_dashboard(),
    })


@support_console_required
def stats_dashboard(request):
    hours_raw = request.GET.get('hours', '24')
    try:
        hours = max(1, min(168, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 24
    return render(request, 'support/stats.html', {
        'page_title': 'Статистика',
        'stats': collect_support_stats(hours=hours),
        'hours': hours,
    })


@support_console_required
def actor_team(request, team_name):
    team = Team.objects.filter(name=team_name).first()
    if team is None:
        raise Http404('Team not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_team_context(team, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='team', team_name=team.name, play_mode='team'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def actor_user(request, user_id):
    from django.contrib.auth.models import User

    user = User.objects.select_related('profile').filter(pk=user_id).first()
    if user is None:
        raise Http404('User not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_user_context(user, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='user', user_id=user.pk, play_mode='personal'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def actor_anon(request, anon_key):
    if not anon_key:
        raise Http404('Anon key required')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_anon_context(anon_key, feed_kwargs=feed_kwargs)
    ctx.update({
        'page_title': ctx['actor_title'],
        'feed_filters': _feed_filters(request),
        'actor_spec': ActorSpec(kind='anon', anon_key=anon_key, play_mode='personal'),
    })
    return render(request, 'support/actor.html', ctx)


@support_console_required
def game_dashboard(request, game_id):
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        raise Http404('Game not found')
    feed_kwargs = _feed_kwargs_from_request(request)
    ctx = build_game_context(game, feed_kwargs=feed_kwargs)
    minutes_raw = request.GET.get('stuck_minutes', '30')
    try:
        stuck_minutes = int(minutes_raw)
    except (TypeError, ValueError):
        stuck_minutes = 30
    ctx.update({
        'page_title': game.outside_name or game.name or game.id,
        'feed_filters': _feed_filters(request),
        'stuck_teams': get_stuck_teams(game, minutes=stuck_minutes),
        'stuck_minutes': stuck_minutes,
    })
    return render(request, 'support/game.html', ctx)


@support_console_required
def preview_game(request, game_id):
    game = Game.objects.filter(pk=game_id).first()
    if game is None:
        raise Http404('Game not found')
    spec = parse_actor_spec(request)
    ctx = build_preview_game_context(game, spec)
    return render(request, 'support/preview_game.html', ctx)


@support_console_required
def preview_task_group(request, game_id, task_group_number):
    spec = parse_actor_spec(request)
    ctx = build_preview_task_group_context(game_id, task_group_number, spec)
    return render(request, 'support/preview_task_group.html', ctx)


@support_console_required
def pending_queue(request):
    return render(request, 'support/pending.html', {
        'page_title': 'Pending',
        'items': get_pending_queue(),
    })


@support_console_required
def live_dashboard(request):
    hours_raw = request.GET.get('hours', '2')
    try:
        hours = max(1, min(24, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 2
    poll_raw = request.GET.get('poll', '30')
    try:
        poll_seconds = max(0, min(300, int(poll_raw)))
    except (TypeError, ValueError):
        poll_seconds = 30
    game_id = (request.GET.get('game') or '').strip() or None
    games, feed = get_live_feed(hours=hours, game_id=game_id)
    return render(request, 'support/live.html', {
        'page_title': 'Live',
        'games': games,
        'feed': feed,
        'hours': hours,
        'poll_seconds': poll_seconds,
        'game_id': game_id or '',
    })


@support_console_required
def live_feed_json(request):
    hours_raw = request.GET.get('hours', '2')
    try:
        hours = max(1, min(24, int(hours_raw)))
    except (TypeError, ValueError):
        hours = 2
    game_id = (request.GET.get('game') or '').strip() or None
    games, feed = get_live_feed(hours=hours, game_id=game_id)
    rows = []
    for item in feed:
        rows.append({
            'time': item.time.isoformat() if item.time else None,
            'kind': item.kind,
            'actor_label': item.actor_label,
            'actor_url': item.actor_url,
            'game_id': item.game_id,
            'status': item.status,
            'detail': item.detail,
            'submission_text': item.submission_text,
            'correct_answer': item.correct_answer,
            'object_id': item.object_id,
            'chain_url': item.chain_url,
        })
    return JsonResponse({
        'games': [g.id for g in games],
        'rows': rows,
    })


@support_console_required
def chain_attempt(request, attempt_id):
    ctx = build_chain_context(attempt_id)
    if not ctx:
        raise Http404('Chain context not found')
    ctx['state_formatter'] = format_chain_state
    ctx['formatted_chain_states'] = {
        row.game_mode: format_chain_state(row.state)
        for row in ctx['chain_rows']
    }
    return render(request, 'support/chain.html', ctx)
