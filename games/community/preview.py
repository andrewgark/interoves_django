"""Link previews (OpenGraph) for player, team and invite pages.

Invite links are mostly shared in messengers, where the preview is the
first thing the friend sees.
"""

from django.templatetags.static import static

from games.site.http.tags.filters import ru_plural
from games.site.pages.shared import share_url

FALLBACK_IMAGE = 'img/brand/exports/og-1200x630.png'


def og(request, *, title, description, image_url=''):
    return {
        'title': title,
        'description': description,
        'url': share_url(request, request.path),
        # Only provider-hosted or uploaded avatars reach here (see
        # visibility); anything else falls back to the brand card. Uploaded
        # ones are site-relative, and messengers need an absolute URL.
        'image': share_url(request, image_url) if image_url.startswith('/') else (
            image_url or share_url(request, static(FALLBACK_IMAGE))
        ),
    }


def player_description(stats):
    if stats is None or not stats.has_any:
        return 'Игрок Inter Oves — ежедневные головоломки.'
    games = ', '.join(game.name for game in stats.games[:3])
    parts = ['{} {}'.format(stats.total_played, ru_plural(stats.total_played, 'игра,игры,игр'))]
    if stats.best_day_streak > 1:
        parts.append('серия {} {}'.format(stats.best_day_streak, ru_plural(stats.best_day_streak, 'день,дня,дней')))
    return '{} · {}'.format(' · '.join(parts), games)


def team_description(members_count, stats):
    text = 'Команда Inter Oves · {} {}'.format(
        members_count, ru_plural(members_count, 'участник,участника,участников'),
    )
    if stats is not None and stats.has_any:
        text += ' · {} {}'.format(stats.total_played, ru_plural(stats.total_played, 'игра,игры,игр'))
    return text
