"""Link previews (OpenGraph) for messengers.

Every page built on site/base.html gets a preview (page_og); player, team
and invite pages pass their own ``og``. Links are mostly shared in
messengers, where the preview is the first thing the friend sees.
"""

import re

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


SITE_TITLE = 'Inter Oves'
HUB_TITLE = 'Inter Oves — головоломки каждый день'
HUB_DESCRIPTION = (
    'Ежедневные головоломки со словами — Салатик, Лесенка, Алфавитка, Цензурка — '
    'и Десяточки: командные игры, где можно пользоваться интернетом.'
)
_EDITION_RE = re.compile(r'^\d+(?:\.\d+)?$')


def page_og(request):
    """Preview for a page that did not build its own: chosen by URL.

    The hub and the daily games get their own text; a numbered edition
    (/ladder/123/, and /ladder/first/ that redirects to one) is named but
    never described by its content, which would give the puzzle away.
    Anything else gets an empty title: og_meta falls back to the page title,
    which only the view knows.
    """
    title, description = _page_title_and_description(request.path)
    return og(request, title=title, description=description or HUB_DESCRIPTION)


def _page_title_and_description(path):
    from games.sections.hub import ONBOARDING_GAME_META, SECTION_HUB_META
    from games.sections.paths import ROOT_SECTION_GAME_IDS
    from games.task_group.titles import NUMBERED_EDITION_GAME_IDS

    if path == '/':
        return HUB_TITLE, HUB_DESCRIPTION
    if path == '/games/':
        return 'Десяточки · {}'.format(SITE_TITLE), 'Командные сложные игры, в которых можно пользоваться интернетом.'
    parts = [part for part in path.split('/') if part]
    game_id = parts[0] if parts else ''
    meta = SECTION_HUB_META.get(game_id)
    if meta is None or game_id not in ROOT_SECTION_GAME_IDS:
        return '', ''
    description = meta['description'].rstrip('.') + '.'
    if meta.get('soon_text'):
        description += ' ' + meta['soon_text']
    if len(parts) == 1:
        return '{} · {}'.format(meta['title'], SITE_TITLE), description
    name = ONBOARDING_GAME_META.get(game_id, {}).get('title') or meta.get('pager_label') or meta['title']
    edition = parts[1]
    if _EDITION_RE.match(edition) and game_id in NUMBERED_EDITION_GAME_IDS:
        return '{} №{} · {}'.format(name, edition, SITE_TITLE), description
    return '{} · {}'.format(name, SITE_TITLE), description


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
