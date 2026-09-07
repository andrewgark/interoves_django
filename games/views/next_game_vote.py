"""Public page for the September 2026 next-game donation vote."""
from django.templatetags.static import static
from django.shortcuts import render
from django.views.decorators.http import require_GET

from games.analytics import (
    YANDEX_GOAL_NEXT_GAME_VOTE_RETURN,
    YANDEX_GOAL_NEXT_GAME_VOTE_VIEW,
    queue_pending_goal,
)
from games.next_game_vote import (
    CANDIDATE_SLUGS,
    scoreboard,
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
    canonical = request.build_absolute_uri(request.path)
    return render(request, 'new/next_game_vote.html', {
        'page_title': 'Какую игру сделать следующей?',
        'canonical_url': canonical,
        'og_image_url': request.build_absolute_uri(static('img/brand/exports/next-game-vote-og.png')),
        'board': board,
        'candidates': board['candidates'],
    })
