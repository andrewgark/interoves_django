"""Main UI: hub, games folder, profile, team."""
import hmac
import logging
import secrets
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods


from django.db import transaction
from django.utils import timezone


from games.models import (
    ProfileTeamMembership,
    Project,
    Team,
    TeamInvite,
    TicketRequest,
)
from games.models import TICKET_REQUESTS_PAGE_SIZE
from games.identity import has_profile


from games.site.ui import (
    NEW_UI_PROJECT,
    _main_team_page_urls,
    _merge_nav_project_for_scope,
    _project_base,
    _project_team_page_urls,
    _scoped_project_id,
    _team_invite_create_url,
    _team_invite_url,
    _team_join_redirect,
    _team_page_redirect_for_project,
    _team_redirect,
)

logger = logging.getLogger('games.views.new_ui')

def _post_make_new_team_primary(request):
    """POST make_primary: 1 / true — новая команда активна; 0 / false — оставить текущую."""
    v = (request.POST.get('make_primary') or '1').strip().lower()
    return v not in ('0', 'false', 'no', 'off')


def _team_name_key(value):
    """Comparison key for user-facing team names."""
    return ' '.join((value or '').split()).casefold()


def _find_team_by_input(value):
    """Resolve user input by display name first, then by stable name."""
    name = (value or '').strip()
    key = _team_name_key(name)
    if not key:
        return None
    display_team = next(
        (
            candidate for candidate in Team.objects.all().only('name', 'visible_name')
            if _team_name_key(candidate.visible_name or candidate.name) == key
        ),
        None,
    )
    if display_team:
        return display_team
    return Team.objects.filter(name=name).first()


def _team_display_name_taken(value, *, exclude_name=None):
    key = _team_name_key(value)
    if not key:
        return False
    teams = Team.objects.all()
    if exclude_name is not None:
        teams = teams.exclude(pk=exclude_name)
    return any(_team_name_key(team.visible_name or team.name) == key for team in teams.only('name', 'visible_name'))


def _member_teams_active_first(profile):
    """Все команды членства; активная первая (для переключателя на странице команды)."""
    rows = list(
        Team.objects.filter(member_links__profile=profile).distinct().order_by('visible_name', 'name')
    )
    tid = profile.team_on_id
    if not tid:
        return rows, []
    primary = next((t for t in rows if t.pk == tid), None)
    if not primary:
        return rows, []
    others = [t for t in rows if t.pk != tid]
    return [primary] + others, others


def _team_ticket_requests_page(request, team, *, page_param='tr_page', per_page=TICKET_REQUESTS_PAGE_SIZE):
    empty = {
        'ticket_requests_page': None,
        'ticket_requests_paginator': None,
        'ticket_requests_is_paginated': False,
        'ticket_requests_page_qs_prefix': '?',
        'ticket_requests_page_size': per_page,
        'ticket_requests_total': 0,
        'ticket_requests_page_param': page_param,
        'ticket_requests_open': False,
    }
    if not team:
        return empty
    paginator = Paginator(
        TicketRequest.objects.filter(team=team).order_by('-time'),
        per_page,
    )
    page_obj = paginator.get_page(request.GET.get(page_param) or 1)
    qs = request.GET.copy()
    try:
        qs.pop(page_param, None)
    except Exception:
        pass
    rest = qs.urlencode()
    return {
        'ticket_requests_page': page_obj,
        'ticket_requests_paginator': paginator,
        'ticket_requests_is_paginated': paginator.num_pages > 1,
        'ticket_requests_page_qs_prefix': ('?' + rest + '&') if rest else '?',
        'ticket_requests_page_size': per_page,
        'ticket_requests_total': paginator.count,
        'ticket_requests_page_param': page_param,
        'ticket_requests_open': bool(request.GET.get(page_param)),
    }


def _team_ticket_requests_ajax_response(request, team):
    """Partial HTML for ticket-request history pagination (no full page reload)."""
    ctx = _team_ticket_requests_page(request, team)
    if not ctx.get('ticket_requests_page'):
        return JsonResponse({'html': ''})
    html = render(request, 'ui/team_ticket_requests.html', ctx).content.decode('utf-8')
    page_obj = ctx['ticket_requests_page']
    return JsonResponse({
        'html': html,
        'page': page_obj.number,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'num_pages': ctx['ticket_requests_paginator'].num_pages,
        'total': ctx['ticket_requests_total'],
    })


def _new_team_ui_context(request):
    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    scoped = _scoped_project_id(request)
    if scoped:
        get_object_or_404(Project, id=scoped)
        base = _project_base(scoped)
        back = request.build_absolute_uri(base + '/team/')
        url_map = _project_team_page_urls(scoped)
    else:
        back = request.build_absolute_uri('/team/')
        url_map = _main_team_page_urls()
    teams = sorted(
        Team.objects.filter(is_hidden=False),
        key=lambda t: (_team_name_key(t.visible_name or t.name), _team_name_key(t.name)),
    )
    profile = request.user.profile
    profile.repair_primary_team()
    member_teams, member_teams_others = _member_teams_active_first(profile)
    secondary_teams = list(profile.other_member_teams()) if profile.team_on_id else []
    team_invite = None
    team_invite_url = ''
    if profile.team_on:
        team_invite = (
            TeamInvite.objects.filter(team=profile.team_on, revoked_at__isnull=True)
            .order_by('-created_at').first()
        )
        if team_invite:
            team_invite_url = _team_invite_url(
                request, team_invite.token,
                team_invite.team.project_id if team_invite.team.project_id != NEW_UI_PROJECT else None,
            )
    ctx = {
        'project': project,
        'teams': teams,
        'new_team_url': back,
        'member_teams': member_teams,
        'member_teams_others': member_teams_others,
        'secondary_teams': secondary_teams,
        'team_primary_modal': len(member_teams) > 0,
        'team_invite': team_invite,
        'team_invite_url': team_invite_url,
        'team_invite_create_url': _team_invite_create_url(
            request,
            profile.team_on.project_id if profile.team_on and profile.team_on.project_id != NEW_UI_PROJECT else None,
        ),
        **url_map,
    }
    return _merge_nav_project_for_scope(ctx, request, scoped)


@login_required
@require_http_methods(['GET'])
def new_team(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        scoped = _scoped_project_id(request)
        if scoped:
            return redirect('project_hub', project_id=scoped)
        return redirect('new_hub')
    team = request.user.profile.team_on
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('tr_page'):
        if not team:
            return JsonResponse({'html': ''})
        return _team_ticket_requests_ajax_response(request, team)
    ctx = _new_team_ui_context(request)
    ctx['team_section'] = 'hub'
    ctx['page_title'] = 'Команда'
    if team:
        ctx.update(_team_ticket_requests_page(request, team))
    return render(request, 'ui/team.html', ctx)


@login_required
@require_http_methods(['GET'])
def new_team_join_page(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Сначала войдите и создайте профиль.')
        scoped = _scoped_project_id(request)
        if scoped:
            return redirect('project_hub', project_id=scoped)
        return redirect('new_hub')
    ctx = _new_team_ui_context(request)
    ctx['team_section'] = 'join'
    ctx['page_title'] = 'Вступить в команду'
    return render(request, 'ui/team.html', ctx)


@require_http_methods(['GET'])
def new_team_invite(request, token, project_id=None):
    invite = TeamInvite.objects.select_related('team').filter(
        token=token, revoked_at__isnull=True,
    ).first()
    if not invite:
        return render(request, 'new/team_invite.html', {
            'invite_invalid': True,
            'page_title': 'Приглашение в команду',
        }, status=404)
    if project_id and invite.team.project_id != project_id:
        return render(request, 'new/team_invite.html', {
            'invite_invalid': True,
            'page_title': 'Приглашение в команду',
        }, status=404)
    login_base = _project_base(project_id) if project_id else '/'
    login_query = urlencode({'login': '1', 'next': request.get_full_path()})
    return render(request, 'new/team_invite.html', {
        'team_invite': invite,
        'team_invite_token': token,
        'team_invite_project_id': project_id,
        'team_invite_login_url': '{}?{}'.format(login_base, login_query),
        'page_title': 'Приглашение в команду',
    })


@login_required
@require_http_methods(['POST'])
def new_team_invite_accept(request, token, project_id=None):
    invite = TeamInvite.objects.select_related('team').filter(
        token=token, revoked_at__isnull=True,
    ).first()
    if not invite or not has_profile(request.user):
        return render(request, 'new/team_invite.html', {
            'invite_invalid': True,
            'page_title': 'Приглашение в команду',
        }, status=404)
    if project_id and invite.team.project_id != project_id:
        raise Http404()
    team = invite.team
    profile = request.user.profile
    if ProfileTeamMembership.objects.filter(profile=profile, team=team).exists():
        messages.info(request, 'Вы уже состоите в этой команде.')
    else:
        with transaction.atomic():
            profile.team_requested = None
            profile.join_accept_as_primary = True
            profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
            profile.add_team_membership(team, make_primary=not profile.team_on_id)
        messages.success(request, 'Вы вступили в команду.')
    return _team_page_redirect_for_project(team.project_id)


@login_required
@require_http_methods(['POST'])
def new_team_invite_create(request, project_id=None):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    scoped = _scoped_project_id(request)
    if scoped and team.project_id != scoped:
        raise Http404()
    with transaction.atomic():
        locked_team = Team.objects.select_for_update().get(pk=team.pk)
        TeamInvite.objects.filter(team=locked_team, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        TeamInvite.objects.create(
            team=locked_team,
            token=secrets.token_urlsafe(32),
            created_by=request.user,
        )
    messages.success(request, 'Новая ссылка-приглашение создана.')
    return _team_redirect(request)


@login_required
@require_http_methods(['GET'])
def new_team_name_check(request, project_id=None):
    name = (request.GET.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': True, 'available': False, 'reason': 'empty'})
    return JsonResponse({'ok': True, 'available': not _team_display_name_taken(name)})


@login_required
@require_http_methods(['GET'])
def new_team_info(request, project_id=None):
    name = (request.GET.get('name') or '').strip()
    team = _find_team_by_input(name)
    if not team:
        return JsonResponse({'ok': True, 'exists': False})
    return JsonResponse({'ok': True, 'exists': True, 'n_users': team.get_n_users_on(), 'visible_name': team.visible_name})


@login_required
@require_http_methods(['GET', 'POST'])
def new_team_create(request, project_id=None):
    if not has_profile(request.user):
        raise Http404()
    if request.method == 'GET':
        ctx = _new_team_ui_context(request)
        ctx['team_section'] = 'create'
        ctx['page_title'] = 'Создать команду'
        return render(request, 'ui/team.html', ctx)
    scoped = _scoped_project_id(request)
    project = get_object_or_404(Project, id=scoped or NEW_UI_PROJECT)
    name = (request.POST.get('name') or '').strip()
    if not name or _team_display_name_taken(name):
        raise Http404()
    referer_name = (request.POST.get('referer') or '').strip()
    referer = None
    if referer_name:
        referer = _find_team_by_input(referer_name)
        if referer and referer.is_hidden:
            referer = None
    team = Team(name=name, project=project, referer=referer)
    team.save()
    request.user.profile.add_team_membership(team, make_primary=_post_make_new_team_primary(request))
    request.user.profile.team_requested = None
    request.user.profile.join_accept_as_primary = True
    request.user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    return _team_redirect(request)


@login_required
@require_http_methods(['POST'])
def new_team_request_join(request, project_id=None):
    if not has_profile(request.user) or request.user.profile.team_requested:
        raise Http404()
    name = (request.POST.get('name') or '').strip()
    team = _find_team_by_input(name)
    if not team:
        raise Http404()
    if ProfileTeamMembership.objects.filter(profile=request.user.profile, team=team).exists():
        messages.error(request, 'Вы уже в этой команде.')
        return _team_join_redirect(request)
    profile = request.user.profile
    profile.join_accept_as_primary = _post_make_new_team_primary(request)
    profile.team_requested = team
    profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    return _team_redirect(request)


def _team_join_password_matches(stored, provided):
    """Case-insensitive compare; join codes are hex from secrets.token_hex (lowercase)."""
    if not stored or not provided:
        return False
    a = stored.strip().lower().encode('utf-8')
    b = provided.strip().lower().encode('utf-8')
    return hmac.compare_digest(a, b)


@login_required
@require_http_methods(['POST'])
def new_team_join_by_password(request, project_id=None):
    if not has_profile(request.user):
        messages.error(request, 'Нельзя вступить в команду сейчас.')
        return _team_join_redirect(request)
    name = (request.POST.get('name') or '').strip()
    password = (request.POST.get('password') or '').strip()
    team = _find_team_by_input(name)
    if not team:
        messages.error(request, 'Команда не найдена.')
        return _team_join_redirect(request)
    stored = (team.join_password or '').strip()
    if not stored:
        messages.error(
            request,
            'У команды не задан код для быстрого входа. Капитан может задать его на странице команды.',
        )
        return _team_join_redirect(request)
    if not password or not _team_join_password_matches(stored, password):
        messages.error(request, 'Неверный пароль.')
        return _team_join_redirect(request)
    if ProfileTeamMembership.objects.filter(profile=request.user.profile, team=team).exists():
        messages.info(request, 'Вы уже в этой команде.')
        return _team_join_redirect(request)
    request.user.profile.add_team_membership(team, make_primary=_post_make_new_team_primary(request))
    request.user.profile.team_requested = None
    request.user.profile.join_accept_as_primary = True
    request.user.profile.save(update_fields=['team_requested', 'join_accept_as_primary'])
    messages.success(request, 'Вы вступили в команду.')
    return _team_redirect(request)


@login_required
@require_http_methods(['POST'])
def new_team_set_primary(request, project_id=None):
    if not has_profile(request.user):
        raise Http404()
    team_pk = (request.POST.get('team') or '').strip()
    team = get_object_or_404(Team, pk=team_pk)
    if not request.user.profile.set_primary_team(team):
        messages.error(request, 'Нет доступа к этой команде.')
    return _team_redirect(request)


@login_required
@require_http_methods(['GET', 'POST'])
def new_team_password(request, project_id=None):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    if request.method == 'GET':
        return JsonResponse({'ok': True, 'password': team.join_password or ''})
    password = (request.POST.get('password') or '').strip()
    if not password:
        raise Http404()
    team.join_password = password
    team.save(update_fields=['join_password'])
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def new_team_rename(request, project_id=None):
    if not has_profile(request.user) or not request.user.profile.team_on:
        raise Http404()
    team = request.user.profile.team_on
    visible_name = (request.POST.get('visible_name') or '').strip()
    if not visible_name:
        messages.error(request, 'Название не может быть пустым.')
        return _team_redirect(request)
    if _team_display_name_taken(visible_name, exclude_name=team.pk):
        messages.error(request, 'Это название уже занято другой командой.')
        return _team_redirect(request)
    team.visible_name = visible_name
    team.save(update_fields=['visible_name'])
    messages.success(request, 'Название команды обновлено.')
    return _team_redirect(request)

__all__ = [
    '_post_make_new_team_primary',
    '_team_name_key',
    '_find_team_by_input',
    '_team_display_name_taken',
    '_member_teams_active_first',
    '_team_ticket_requests_page',
    '_team_ticket_requests_ajax_response',
    '_new_team_ui_context',
    'new_team',
    'new_team_join_page',
    'new_team_invite',
    'new_team_invite_accept',
    'new_team_invite_create',
    'new_team_name_check',
    'new_team_info',
    'new_team_create',
    'new_team_request_join',
    '_team_join_password_matches',
    'new_team_join_by_password',
    'new_team_set_primary',
    'new_team_password',
    'new_team_rename',
]
