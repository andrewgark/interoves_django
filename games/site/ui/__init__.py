"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse





from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.sections.project_navigation import (
    main_team_page_urls,
    project_base,
    project_team_page_urls,
    project_urls_context,
)
from games.play_mode import (
    default_play_mode,
    get_play_mode,
    session_play_mode_key,
)
from games.sections.navigation import section_ui_context
from games.results.table.access import (
    anon_key_from_request,
)
from games.results.table.tables import (
    ResultsTaskGroupHeader,
)
from games.models import (
    Game,
    HTMLPage,
    Project,
)
from games.word_salad import (
    WORD_SALAD_GAME_ID,
)


logger = logging.getLogger('games.views.new_ui')

def _anon_key_from_request(request):
    """Canonical browser anonymous identity. GET/POST/header/URL are not authority."""
    return anon_key_from_request(request)


def _age_gate_context(game, task_group=None, *, back_url='/'):
    """Client-side 18+ gate: localStorage key + back link for the leave button."""
    game_flag = bool(getattr(game, 'is_18_plus', False))
    tg_flag = bool(task_group and getattr(task_group, 'is_18_plus', False))
    if not game_flag and not tg_flag:
        return {}
    if game_flag:
        storage_key = 'interoves_18plus_game_{}'.format(game.id)
    else:
        storage_key = 'interoves_18plus_tg_{}'.format(task_group.id)
    return {
        'show_18plus_gate': True,
        'age_gate_storage_key': storage_key,
        'age_gate_back_url': back_url,
    }


def _ru_plural_form_int(n, one, few, many):
    n = abs(int(n))
    n_mod_100 = n % 100
    if 11 <= n_mod_100 <= 14:
        return many
    n_mod_10 = n % 10
    if n_mod_10 == 1:
        return one
    if 2 <= n_mod_10 <= 4:
        return few
    return many


def _ru_iz_punkt_word(n):
    # родительный падеж после "из N ..."
    return _ru_plural_form_int(n, 'пункта', 'пунктов', 'пунктов')


_ResultsTaskGroupHeader = ResultsTaskGroupHeader


NEW_UI_PROJECT = 'main'
NEW_UI_SECTIONS_PROJECT = 'sections'
PALINDROMES_GAME_ID = 'palindromes'
# Разделы с собственным туториалом (модалка правил)
SECTION_RULES_GAME_IDS = (
    'palindromes', 'replacements', 'walls', 'ladder', 'alphabetty', 'censorly',
    'week_task', WORD_SALAD_GAME_ID,
)


def _section_tutorial_html_for_game(game):
    """Rules HTML for the ? modal on a section play page.

    Hub pages read ``Game.section_default_rules``. Play pages used to look up
    only ``section_tutorial_<game.id>``. After salad was renamed from
    ``word_salad``, that convention no longer matches the stored HTMLPage
    (``section_tutorial_word_salad``), so the modal opened empty.
    """
    try:
        related = game.section_default_rules
    except HTMLPage.DoesNotExist:
        related = None
    if related is not None and (related.html or '').strip():
        return related.html
    names = []
    if game.id in SECTION_RULES_GAME_IDS:
        names.append('section_tutorial_' + game.id)
    related_id = getattr(game, 'section_default_rules_id', None)
    if related_id and related_id not in names:
        names.append(related_id)
    if not names:
        return None
    pages = {
        page.name: page.html or ''
        for page in HTMLPage.objects.filter(name__in=names)
    }
    for name in names:
        html = pages.get(name) or ''
        if html.strip():
            return html
    return None


def _project_base(project_id: str | None) -> str:
    return project_base(
        project_id,
        main_project_id=NEW_UI_PROJECT,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


def _redirect_with_current_query(request, path, *, permanent=True):
    query = request.META.get('QUERY_STRING')
    if query:
        path = '{}?{}'.format(path, query)
    return redirect(path, permanent=permanent)


def _canonical_project_game_path(game, suffix=''):
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        return None
    base = _project_base(game.project_id)
    return '{}/games/{}/{}'.format(base, game.id, suffix)


def _root_project_game_redirect(request, game, suffix=''):
    if game.project_id in (NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT):
        return None
    path = _canonical_project_game_path(game, suffix)
    if path is None:
        return None
    return _redirect_with_current_query(request, path)


def legacy_results_redirect(request, game_id, mode='general'):
    game = get_object_or_404(Game, id=game_id)
    suffix = 'tournament-results/' if mode == 'tournament' else 'results/'
    path = _canonical_project_game_path(game, suffix)
    if path is None:
        raise Http404()
    return _redirect_with_current_query(request, path)


def legacy_tournament_results_redirect(request, game_id):
    return legacy_results_redirect(request, game_id, mode='tournament')


def _task_group_live_state_url(game):
    if game.project_id not in (NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT):
        return reverse('project_game_live_state', kwargs={
            'project_id': game.project_id,
            'game_id': game.id,
        })
    from games.sections.paths import is_root_section_game
    if is_root_section_game(game.id):
        return '/{}/live-state/'.format(game.id)
    return reverse('ui_task_group_live_state', kwargs={'game_id': game.id})


def _project_urls_context(project_id: str | None):
    return project_urls_context(
        project_id,
        main_project_id=NEW_UI_PROJECT,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


def _section_ui_context(game):
    """Common section flags and URLs; format-specific views add their own extras."""
    return section_ui_context(
        game,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
        ladder_game_id=LADDER_GAME_ID,
        alphabetty_game_id=ALPHABETTY_GAME_ID,
        salad_game_id=WORD_SALAD_GAME_ID,
    )


def _scoped_project_id(request) -> str | None:
    """Non-main project id when current URL is under /<project_id>/… (e.g. glowbyte)."""
    match = getattr(request, 'resolver_match', None)
    if not match:
        return None
    pid = (match.kwargs or {}).get('project_id')
    if not pid:
        return None
    pid = str(pid).strip()
    return pid or None


def _main_team_page_urls():
    return main_team_page_urls()


def _project_team_page_urls(project_id: str):
    return project_team_page_urls(project_id)


def _merge_nav_project_for_scope(ctx: dict, request, scoped_id: str | None):
    """Header logo: scoped project; team hub still uses main project in `project` for tickets/teams DB."""
    if not scoped_id:
        return ctx
    nav_project = get_object_or_404(Project, id=scoped_id)
    ctx['nav_project'] = nav_project
    ctx.update(_project_urls_context(scoped_id))
    return ctx


def _profile_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile', project_id=scoped)
    return redirect('new_profile')


def _reports_list_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile_reports', project_id=scoped)
    return redirect('new_profile_reports')


def _report_detail_redirect(request, report_id):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_profile_report_detail', project_id=scoped, report_id=report_id)
    return redirect('new_profile_report_detail', report_id=report_id)


def _team_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_team', project_id=scoped)
    return redirect('new_team')


def _team_join_redirect(request):
    scoped = _scoped_project_id(request)
    if scoped:
        return redirect('project_team_join_page', project_id=scoped)
    return redirect('new_team_join_page')


def _team_page_redirect_for_project(project_id):
    if project_id and project_id != NEW_UI_PROJECT:
        return redirect('project_team', project_id=project_id)
    return redirect('new_team')


def _team_invite_url(request, token, project_id=None):
    if project_id and project_id != NEW_UI_PROJECT:
        path = reverse('project_team_invite', kwargs={'project_id': project_id, 'token': token})
    else:
        path = reverse('new_team_invite', kwargs={'token': token})
    return request.build_absolute_uri(path)


def _team_invite_create_url(request, project_id=None):
    if project_id and project_id != NEW_UI_PROJECT:
        return reverse('project_team_invite_create', kwargs={'project_id': project_id})
    return reverse('new_team_invite_create')


def _session_play_mode_key(project_id):
    return session_play_mode_key(project_id)


def _default_play_mode(project_id):
    return default_play_mode(
        project_id,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


def _get_play_mode(request, project_id):
    return get_play_mode(
        request,
        project_id,
        sections_project_id=NEW_UI_SECTIONS_PROJECT,
    )


__all__ = [
    '_anon_key_from_request',
    '_age_gate_context',
    '_ru_plural_form_int',
    '_ru_iz_punkt_word',
    '_ResultsTaskGroupHeader',
    'NEW_UI_PROJECT',
    'NEW_UI_SECTIONS_PROJECT',
    'PALINDROMES_GAME_ID',
    'SECTION_RULES_GAME_IDS',
    '_section_tutorial_html_for_game',
    '_project_base',
    '_redirect_with_current_query',
    '_canonical_project_game_path',
    '_root_project_game_redirect',
    'legacy_results_redirect',
    'legacy_tournament_results_redirect',
    '_task_group_live_state_url',
    '_project_urls_context',
    '_section_ui_context',
    '_scoped_project_id',
    '_main_team_page_urls',
    '_project_team_page_urls',
    '_merge_nav_project_for_scope',
    '_profile_redirect',
    '_reports_list_redirect',
    '_report_detail_redirect',
    '_team_redirect',
    '_team_join_redirect',
    '_team_page_redirect_for_project',
    '_team_invite_url',
    '_team_invite_create_url',
    '_session_play_mode_key',
    '_default_play_mode',
    '_get_play_mode',
]
