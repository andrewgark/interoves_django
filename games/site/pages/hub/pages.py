"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from games.club.next_game import PHASE_CLOSED, campaign_phase
from games.daily.catalog.alphabetty import ALPHABETTY_GAME_ID
from games.daily.catalog.ladder import LADDER_GAME_ID, get_ladder_hub_context
from games.daily.page.section import WORD_SALAD_GAME_ID
from games.daily.timing.transitions import next_daily_content_transition_for_games
from games.models import Game, Project
from games.sections.hub import get_desyatochki_hub_context
from games.site.http.middleware.request_timing import timing_phase
from games.site.pages import NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT, _project_base, _project_urls_context
from games.site.pages.game.access import _games_list_card_context, _new_folder_games
from games.site.pages.game.pages import _render_section_game_page
from games.site.pages.game.progress import _play_url_for_task_group
from games.site.pages.hub.actor import team_for_user
from games.site.pages.hub.cards import (
    _build_hub_section_cards,
    _folder_by_slug,
    _get_ladder_game,
    _is_first_time_player,
    _ladder_latest_play_url,
    _ladder_published_numbers,
    _onboarding_game_cards,
    _onboarding_starter_salad_url,
    get_section_games,
)
from games.site.pages.hub.links import (
    MAIN_COMMUNITY_LINKS,
    MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL,
    MAIN_INTERESTING_LINKS,
    project_desyatochki_rules_chat_url,
    project_hub_community_links,
)
from games.site.views.pages.public.main import MainPageView

logger = logging.getLogger('games.views.new_ui')


def hub(request):
    # Phases show up in slow_request logs: the hub is the most frequent slow
    # page and its time was all "unaccounted".
    with timing_phase(request, 'hub_section_games'):
        project = Project.objects.filter(id=NEW_UI_PROJECT).first()
        section_games = get_section_games(request)
    with timing_phase(request, 'hub_daily_cards'):
        hub_groups = _build_hub_section_cards(request, team=team_for_user(request.user))
    with timing_phase(request, 'hub_next_transition'):
        live_next_transition_at = next_daily_content_transition_for_games(
            card.get('game') for card in hub_groups['daily_hub_cards']
        )
    with timing_phase(request, 'hub_desyatochki'):
        view = MainPageView()
        view.project_name = NEW_UI_PROJECT
        desyatochki_games = view.get_games_list(request)
        desyatochki_card = get_desyatochki_hub_context(desyatochki_games) if desyatochki_games else None
    with timing_phase(request, 'hub_onboarding'):
        show_first_visit_onboarding = _is_first_time_player(request)
        onboarding_salad_url = _onboarding_starter_salad_url(hub_groups['daily_hub_cards'])
    with timing_phase(request, 'hub_games_list'):
        games_list_context = _games_list_card_context(request)
    with timing_phase(request, 'hub_render'):
        return render(request, 'ui/hub.html', {
            'project': project,
            'section_games': section_games,
            **hub_groups,
            'desyatochki_card': desyatochki_card,
            'show_first_visit_onboarding': show_first_visit_onboarding,
            'onboarding_salad_url': onboarding_salad_url,
            **games_list_context,
            'page_title': 'Interoves',
            'show_sections_nav': True,
            'live_next_transition_at': live_next_transition_at,
            'show_desyatochki_pay_cta': True,
            'show_order_banner': True,
            'desyatochki_participants_chat_url': MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL,
            'desyatochki_rules_participants_chat_url': MAIN_DESYATOCHKI_PARTICIPANTS_CHAT_URL,
            'community_links': MAIN_COMMUNITY_LINKS,
            'interesting_links': MAIN_INTERESTING_LINKS,
            'show_donate_cta': True,
            'show_next_game_vote_cta': campaign_phase() != PHASE_CLOSED,
            **_project_urls_context(NEW_UI_PROJECT),
        })


def start(request):
    """Focused landing page for first-time and advertising traffic."""
    project = Project.objects.filter(id=NEW_UI_PROJECT).first()
    hub_groups = _build_hub_section_cards(request, team=team_for_user(request.user))
    onboarding_cards = _onboarding_game_cards(hub_groups['daily_hub_cards'])
    salad_card = next(
        (card for card in onboarding_cards if card['id'] == WORD_SALAD_GAME_ID),
        None,
    )
    if salad_card:
        salad_card['play_url'] = _onboarding_starter_salad_url(hub_groups['daily_hub_cards'])

    return render(request, 'site/start.html', {
        'project': project,
        'onboarding_salad_card': salad_card,
        'onboarding_alternative_cards': [
            card for card in onboarding_cards if card['id'] != WORD_SALAD_GAME_ID
        ],
        'page_title': 'С чего начать',
        'show_sections_nav': False,
        'focused_header': True,
        **_project_urls_context(NEW_UI_PROJECT),
    })


def folder(request, slug):
    folder = _folder_by_slug(slug)
    if not folder:
        raise Http404()
    if folder['type'] == 'games':
        return _new_folder_games(request)
    raise Http404()


def project_hub(request, project_id):
    """Entry point for project-scoped UI, e.g. /glowbyte/"""
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)
    base = _project_base(project.id)
    view = MainPageView()
    view.project_name = project.id
    project_games = view.get_games_list(request)
    desyatochki_card = get_desyatochki_hub_context(project_games, base=base) if project_games else None
    return render(request, 'ui/hub.html', {
        'project': project,
        'desyatochki_card': desyatochki_card,
        'section_games': [],
        'page_title': project.id,
        'show_sections_nav': False,
        **_games_list_card_context(request),
        'community_links': project_hub_community_links(project.id),
        'desyatochki_rules_participants_chat_url': project_desyatochki_rules_chat_url(project.id),
        **_project_urls_context(project.id),
    })


def project_folder_games(request, project_id):
    """Games list inside a project scope, e.g. /glowbyte/games/ (same UI as /games/)."""
    project_id = (project_id or '').strip()
    project = get_object_or_404(Project, id=project_id)

    view = MainPageView()
    view.project_name = project.id
    view.games_per_page = 20
    all_games = view.get_games_list(request)
    card_ctx = _games_list_card_context(request)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from django.core.paginator import Paginator

        page = int(request.GET.get('page', 1))
        paginator = Paginator(all_games, view.games_per_page)
        games_page = paginator.get_page(page)
        games_html = render(request, 'ui/games_list_items.html', {
            'games': games_page,
            'game_list_offset': (page - 1) * view.games_per_page,
            **card_ctx,
            **_project_urls_context(project.id),
        }).content.decode('utf-8')
        return JsonResponse({
            'games_html': games_html,
            'page': page,
            'has_next': games_page.has_next(),
            'total_pages': paginator.num_pages,
            'total_games': len(all_games),
        })

    return render(request, 'ui/folder_games.html', {
        'project': project,
        'games': all_games[:view.games_per_page],
        'total_games': len(all_games),
        'games_per_page': view.games_per_page,
        'page_title': 'Десяточки',
        **card_ctx,
        'section_games': [],
        'show_sections_nav': False,
        **_project_urls_context(project.id),
    })


def ladder_today_page(request):
    """Редирект на сегодняшнюю (или последнюю опубликованную) Лесенку."""
    ladder_game = _get_ladder_game()
    if not ladder_game:
        raise Http404()
    team = team_for_user(request.user)
    if not ladder_game.has_access('see_game_preview', team=team):
        raise Http404()
    ctx = get_ladder_hub_context(
        ladder_game,
        published_numbers=_ladder_published_numbers(ladder_game),
    )
    cta_number = ctx.get('ladder_cta_number')
    if not cta_number:
        return redirect('ui_ladder_hub')
    return redirect(_play_url_for_task_group(ladder_game, cta_number))


def ladder_last_page(request):
    """Редирект на последнюю опубликованную Лесенку."""
    ladder_game = _get_ladder_game()
    if not ladder_game:
        raise Http404()
    team = team_for_user(request.user)
    if not ladder_game.has_access('see_game_preview', team=team):
        raise Http404()
    play_url = _ladder_latest_play_url(ladder_game)
    if not play_url:
        return redirect('ui_ladder_hub')
    return redirect(play_url)


def section_last_page(request, game_id):
    """Редирект на последний круг раздела (walls / week_task / …)."""
    if game_id in (LADDER_GAME_ID, ALPHABETTY_GAME_ID):
        raise Http404()
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        raise Http404()
    game = Game.objects.filter(project=project, id=game_id).first()
    if not game:
        raise Http404()
    team = team_for_user(request.user)
    if not game.has_access('see_game_preview', team=team):
        raise Http404()
    from games.sections.hub import _newest_task_group_links
    from games.sections.paths import section_hub_path, section_play_path

    links = list(_newest_task_group_links(game))
    if not links:
        return redirect(section_hub_path(game.id))
    return redirect(section_play_path(game.id, links[0].number))


def ladder_hub_page(request):
    """Архив лесенок: /ladder/ (канонический URL вместо /games/ladder/ и /section/ladder/)."""
    return _render_section_game_page(request, LADDER_GAME_ID)


__all__ = [
    'hub',
    'start',
    'folder',
    'project_hub',
    'project_folder_games',
    'ladder_today_page',
    'ladder_last_page',
    'section_last_page',
    'ladder_hub_page',
]

