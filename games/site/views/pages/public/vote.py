"""Public pages for the next-game donation votes."""
from django.conf import settings
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_GET

from games.club.next_game import (
    CANDIDATE_SLUGS,
    scoreboard,
)
from games.club.next_game_autumn import CANDIDATE_SLUGS as AUTUMN_SLUGS
from games.club.next_game_autumn import LOGIC_EXAMPLES
from games.club.next_game_autumn import scoreboard as autumn_scoreboard
from games.ops.analytics.events import (
    YANDEX_GOAL_NEXT_GAME_AUTUMN_CLICK,
    YANDEX_GOAL_NEXT_GAME_AUTUMN_RETURN,
    YANDEX_GOAL_NEXT_GAME_AUTUMN_VIEW,
    YANDEX_GOAL_NEXT_GAME_VOTE_RETURN,
    YANDEX_GOAL_NEXT_GAME_VOTE_VIEW,
    queue_pending_goal,
)


@require_GET
def next_game_vote_page(request):
    board = scoreboard()
    queue_pending_goal(
        request,
        YANDEX_GOAL_NEXT_GAME_VOTE_VIEW,
        key=YANDEX_GOAL_NEXT_GAME_VOTE_VIEW,
    )
    returned = (request.GET.get('from') or '').strip().lower()
    returned_candidate = (request.GET.get('candidate') or '').strip().lower()
    if returned in ('tribute', 'tribute_return') and returned_candidate in CANDIDATE_SLUGS:
        queue_pending_goal(
            request,
            YANDEX_GOAL_NEXT_GAME_VOTE_RETURN,
            params={'candidate': returned_candidate},
            key='{}:{}'.format(YANDEX_GOAL_NEXT_GAME_VOTE_RETURN, returned_candidate),
        )
    canonical = _public_url(request.path)
    return render(request, 'site/next_game_vote.html', {
        'page_title': 'Какую игру сделать следующей?',
        'canonical_url': canonical,
        'og_image_url': _public_url(static('img/brand/exports/next-game-vote-og.png')),
        'board': board,
        'candidates': board['candidates'],
    })


def _public_url(path):
    """Canonical and share URLs stay on the main site for every entry host.

    The page is also served on ru.interoves.com and the origin sees plain
    HTTP, so request.build_absolute_uri() would name the wrong host/scheme.
    """
    return settings.SITE_BASE_URL + path


def _returned_candidate(request, allowed):
    returned = (request.GET.get('from') or '').strip().lower()
    candidate = (request.GET.get('candidate') or '').strip().lower()
    if returned in ('tribute', 'tribute_return') and candidate in allowed:
        return candidate
    return ''


@require_GET
def next_game_autumn_vote_page(request):
    board = autumn_scoreboard()
    queue_pending_goal(
        request,
        YANDEX_GOAL_NEXT_GAME_AUTUMN_VIEW,
        key=YANDEX_GOAL_NEXT_GAME_AUTUMN_VIEW,
    )
    returned_candidate = _returned_candidate(request, AUTUMN_SLUGS)
    if returned_candidate:
        queue_pending_goal(
            request,
            YANDEX_GOAL_NEXT_GAME_AUTUMN_RETURN,
            params={'candidate': returned_candidate},
            key='{}:{}'.format(YANDEX_GOAL_NEXT_GAME_AUTUMN_RETURN, returned_candidate),
        )
    canonical = _public_url(request.path)
    return render(request, 'site/next_game_autumn_vote.html', {
        'page_title': 'Какую игру сделать следующей?',
        'canonical_url': canonical,
        'og_image_url': _public_url(static('img/brand/exports/og-1200x630.png')),
        'board': board,
        'candidates': board['candidates'],
        'logic_examples': LOGIC_EXAMPLES,
        'click_goal': YANDEX_GOAL_NEXT_GAME_AUTUMN_CLICK,
    })
