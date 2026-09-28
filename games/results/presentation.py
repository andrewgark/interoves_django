"""Presentation helpers for progressive results pages."""

from django.core.paginator import Paginator
from django.shortcuts import render

from games.results.context import attach_results_club_badges


def paginate_results_rows(request, data, per_page=50):
    rows = list(data.get('teams_sorted') or [])
    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    out = dict(data)
    attach_results_club_badges(out)
    out['teams_sorted'] = list(page_obj.object_list)
    out['page_obj'] = page_obj
    out['paginator'] = paginator
    out['is_paginated'] = paginator.num_pages > 1
    query_string = request.GET.copy()
    query_string.pop('page', None)
    rest = query_string.urlencode()
    out['page_qs_prefix'] = ('?' + rest + '&') if rest else '?'
    out['page_size'] = per_page
    out['page_total_rows'] = paginator.count
    out['progressive_results'] = True
    return out


def render_results_rows_partial(
    request, data, *, mode, results_variant='standard', team=None,
    me_personal=None, me_anon_participant=None,
    show_solve_duration=False, show_alphabetty_detail=False,
):
    return render(request, 'new/partials/results_rows.html', {
        'mode': mode,
        'section_results': False,
        'results_variant': results_variant,
        'team': team,
        'me_personal': me_personal,
        'me_anon_participant': me_anon_participant,
        'show_solve_duration': show_solve_duration,
        'show_alphabetty_detail': show_alphabetty_detail,
        **data,
    })
