"""Root-level URL routes for sections-project games (/walls/, …)."""

from django.urls import path, re_path
from django.views.generic import RedirectView

from games.section_paths import STANDARD_ROOT_SECTION_GAME_IDS
from games.views import ui

_SECTION_GAME_ID_RE = '|'.join(STANDARD_ROOT_SECTION_GAME_IDS)
_RESULTS_GAME_ID_RE = 'ladder|alphabetty|' + _SECTION_GAME_ID_RE


def section_root_urlpatterns(
    *,
    section_game_url_name='ui_section_game',
    section_results_url_name='ui_section_results',
    task_group_url_name='ui_section_task_group',
):
    """URL patterns to merge before /games/<id>/ hub routes and /games/<id>/<number>/ play."""
    section_last_url_name = (
        'ui_section_last'
        if section_game_url_name.startswith('ui_')
        else 'new_section_last'
    )
    patterns = [
        re_path(
            r'^(?P<game_id>' + _RESULTS_GAME_ID_RE + r')/results/$',
            ui.section_results_page,
            name=section_results_url_name,
        ),
        path(
            'section/ladder/results/',
            RedirectView.as_view(url='/ladder/results/', permanent=True, query_string=True),
        ),
        re_path(
            r'^section/(?P<game_id>' + _RESULTS_GAME_ID_RE + r')/results/$',
            RedirectView.as_view(url='/%(game_id)s/results/', permanent=True, query_string=True),
        ),
        path(
            'section/ladder/',
            RedirectView.as_view(url='/ladder/', permanent=True, query_string=True),
        ),
        path(
            'section/ladder/<path:rest>',
            RedirectView.as_view(url='/ladder/%(rest)s', permanent=True, query_string=True),
        ),
        path(
            'section/alphabetty/',
            RedirectView.as_view(url='/alphabetty/', permanent=True, query_string=True),
        ),
        path(
            'section/alphabetty/<path:rest>',
            RedirectView.as_view(url='/alphabetty/%(rest)s', permanent=True, query_string=True),
        ),
        re_path(
            r'^section/(?P<game_id>' + _SECTION_GAME_ID_RE + r')/$',
            RedirectView.as_view(url='/%(game_id)s/', permanent=True, query_string=True),
        ),
        re_path(
            r'^section/(?P<game_id>' + _SECTION_GAME_ID_RE + r')/(?P<rest>.+)$',
            RedirectView.as_view(url='/%(game_id)s/%(rest)s', permanent=True, query_string=True),
        ),
        re_path(
            r'^(?P<game_id>' + _SECTION_GAME_ID_RE + r')/$',
            ui.section_game_page,
            name=section_game_url_name,
        ),
        re_path(
            r'^(?P<game_id>' + _SECTION_GAME_ID_RE + r')/last/$',
            ui.section_last_page,
            name=section_last_url_name,
        ),
        re_path(
            r'^(?P<game_id>' + _SECTION_GAME_ID_RE + r')/(?P<task_group_number>\d+(?:\.\d+)?)/$',
            ui.task_group_page,
            name=task_group_url_name,
        ),
    ]

    for game_id in STANDARD_ROOT_SECTION_GAME_IDS:
        patterns += [
            path(
                '{}/progress/'.format(game_id),
                ui.game_task_group_progress,
                {'game_id': game_id},
            ),
            path(
                'games/{}/'.format(game_id),
                RedirectView.as_view(url='/{}/'.format(game_id), permanent=True, query_string=True),
            ),
            re_path(
                r'^games/{}/(?P<rest>.+)$'.format(game_id),
                RedirectView.as_view(url='/{}/%(rest)s'.format(game_id), permanent=True, query_string=True),
            ),
        ]

    return patterns
