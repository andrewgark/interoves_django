"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render





from games.access import game_has_started
from games.models import (
    Project,
)
from games.site.views.pages.public.main import MainPageView
from games.site.views.pages.util import (
    has_profile,
    has_team,
)


from games.site.ui import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    _age_gate_context,
    _project_urls_context,
)

logger = logging.getLogger('games.views.new_ui')

def _games_list_card_context(request):
    """Контекст для new/games_list_items: безопасно для анонима, team согласован с has_team."""
    if not request.user.is_authenticated or not has_profile(request.user):
        return {'games_card_team': None, 'games_card_has_team': False}
    has_t = has_team(request.user)
    return {
        'games_card_team': request.user.profile.team_on if has_t else None,
        'games_card_has_team': has_t,
    }


def _team_for_access(request):
    """Активная команда для access-проверок (None для гостя / без команды)."""
    if not request.user.is_authenticated or not has_profile(request.user):
        return None
    if not has_team(request.user):
        return None
    return request.user.profile.team_on


def _registration_blocks_play(game, team):
    """Нужна регистрация на игру, а у team её нет (включая team=None)."""
    return bool(
        game.has_access('needs_registration', team=team)
        and not game.has_access('is_registered', team=team)
    )


def _announced_game_page_response(request, game, *, back_url, show_sections_nav=True, project=None):
    """Карточка игры из списка десяточек (анонс / нет доступа к заданиям без регистрации)."""
    page_title = game.get_outside_name() if hasattr(game, 'get_outside_name') else (game.outside_name or game.name)
    ctx = {
        'game': game,
        'games': [game],
        'page_title': page_title,
        'back_url': back_url,
        'show_sections_nav': show_sections_nav,
        **_games_list_card_context(request),
        **_project_urls_context(game.project_id),
        **_age_gate_context(game, back_url=back_url),
    }
    if project is not None:
        ctx['project'] = project
    return render(request, 'ui/game_announce_page.html', ctx)


def _maybe_registration_or_announce_response(
    request,
    game,
    *,
    back_url,
    show_sections_nav=True,
    project=None,
    team=None,
):
    """
    Для обычных игр (не sections): карточка анонса вместо заданий, если игра ещё
    не началась или команде нужна регистрация. None — можно показывать задания.
    """
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        return None
    if team is None:
        team = _team_for_access(request)
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    if not game_has_started(game) or _registration_blocks_play(game, team):
        return _announced_game_page_response(
            request,
            game,
            back_url=back_url,
            show_sections_nav=show_sections_nav,
            project=project,
        )
    return None


def _new_folder_games(request):
    view = MainPageView()
    view.project_name = NEW_UI_PROJECT
    view.games_per_page = 20
    all_games = view.get_games_list(request)
    card_ctx = _games_list_card_context(request)

    # AJAX pagination (append cards on window scroll near bottom)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.core.paginator import Paginator
        page = int(request.GET.get('page', 1))
        paginator = Paginator(all_games, view.games_per_page)
        games_page = paginator.get_page(page)
        games_html = render(request, 'ui/games_list_items.html', {
            'games': games_page,
            'game_list_offset': (page - 1) * view.games_per_page,
            **card_ctx,
        }).content.decode('utf-8')
        return JsonResponse({
            'games_html': games_html,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages,
            'total_games': len(all_games),
        })

    project = get_object_or_404(Project, id=NEW_UI_PROJECT)
    return render(request, 'ui/folder_games.html', {
        'project': project,
        'games': all_games[:view.games_per_page],
        'total_games': len(all_games),
        'games_per_page': view.games_per_page,
        'page_title': 'Десяточки',
        **card_ctx,
        'show_sections_nav': True,
        **_project_urls_context(project.id),
    })



__all__ = [
    '_games_list_card_context',
    '_team_for_access',
    '_registration_blocks_play',
    '_announced_game_page_response',
    '_maybe_registration_or_announce_response',
    '_new_folder_games',
]
