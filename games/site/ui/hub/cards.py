"""Main UI: hub, games folder, profile, team."""
import logging

from django.conf import settings


from django.db.models import Count, Q
from django.utils import timezone


from games.ops.analytics.identity import (
    browser_anon_key,
    is_valid_anon_key,
)
from games.daily.page.section import (
    WORD_SALAD_GAME_ID,
    filter_published_links,
    is_scheduled_game,
    publish_at_for,
    scheduled_number_is_public,
    visible_links,
    MOSCOW,
)
from games.daily.page.archive import (
    build_daily_archive_items,
    refresh_daily_archive_context,
)
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.sections.hub import (
    HUB_DAILY_SECTION_IDS,
    HUB_FROM_DESYATOCHKI_SECTION_IDS,
    ONBOARDING_GAME_IDS,
    ONBOARDING_GAME_META,
    SECTION_HUB_META,
    get_scheduled_section_hub_card,
    get_training_section_hub_context,
)
from games.models import (
    Attempt,
    Game,
    GameTaskGroup,
    HintAttempt,
    PlayerCompletedGame,
    PlayerStartedGame,
    Project,
    Task,
)
from games.puzzles.raddle.engine import (
    parse_raddle_data,
)
from games.puzzles.salad.rules import archive_card_meta as word_salad_archive_card_meta
from games.identity import has_profile
from games.site.session.play_mode import effective_play_mode


from games.site.ui import (
    NEW_UI_SECTIONS_PROJECT,
    _anon_key_from_request,
    _get_play_mode,
)
from games.site.ui.game.progress import (
    _game_page_progress_context,
    _game_task_group_links,
    _play_url_for_task_group,
    _player_visible_task_group_links,
    _resolve_game_page_actor,
    _task_group_progress_payload,
    _task_group_results_url,
)

logger = logging.getLogger('games.views.new_ui')

# Один общий раздел «Десяточки» (игры из project main); остальные — по одной игре из project sections.
NEW_UI_FOLDERS = [
    {
        'slug': 'games',
        'title': 'Десяточки',
        'description': 'Командные сложные игры, в которых можно пользоваться интернетом',
        'type': 'games',
    },
]


# Same order as hub "Задания из Десяточек" (replacements → walls → palindromes).
_SECTION_NAV_ORDER = {
    game_id: i for i, game_id in enumerate(HUB_FROM_DESYATOCHKI_SECTION_IDS)
}


def get_section_games(request):
    """Публичные дополнительные разделы с доступом на превью для навигации."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    if not project:
        return []
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    games_list = [
        g for g in Game.objects.filter(
            project=project,
            id__in=HUB_FROM_DESYATOCHKI_SECTION_IDS,
        )
        if g.has_access('see_game_preview', team=team)
    ]
    return sorted(games_list, key=lambda g: _SECTION_NAV_ORDER.get(g.id, 99))


def _hub_section_task_group_links(game):
    """Круги раздела для списка на странице — новые сверху."""
    qs = (
        GameTaskGroup.objects.filter(game=game)
        .select_related('task_group')
        .annotate(n_tasks=Count('task_group__tasks', filter=Q(task_group__tasks__is_removed=False)))
    )
    if is_scheduled_game(game.id):
        return visible_links(qs, game, reverse=True)
    return GameTaskGroup.order_queryset_by_number(qs, reverse=True)


def _build_hub_section_cards(request, *, team):
    """Карточки разделов на главной, сгруппированные для двух блоков."""
    project = Project.objects.filter(id=NEW_UI_SECTIONS_PROJECT).first()
    by_id = {}
    if project:
        for game_id in (*HUB_DAILY_SECTION_IDS, *HUB_FROM_DESYATOCHKI_SECTION_IDS):
            game = Game.objects.filter(id=game_id, project=project).first()
            if not game or not game.has_access('see_game_preview', team=team):
                continue
            if is_scheduled_game(game_id):
                card = get_scheduled_section_hub_card(
                    game,
                    published_numbers=_published_numbers(game),
                )
            else:
                card = get_training_section_hub_context(game)
            play_mode, _ = _get_play_mode(request, game.project_id)
            play_mode = effective_play_mode(play_mode, game, user=request.user)
            card.update(_game_page_progress_context(request, game, play_mode))
            if game_id == 'censorly':
                card['new_label'] = 'NEW'
            # The streak is rendered from the initial response, so project the
            # CTA's solved state here as well instead of waiting for one JSON
            # request per card after the page has loaded.
            if card.get('cta_number') and card.get('load_task_group_progress'):
                actor_team, actor_user, anon_key = _resolve_game_page_actor(request, play_mode)
                try:
                    cta_number = str(card['cta_number'])
                    links = [
                        link for link in _player_visible_task_group_links(game)
                        if str(link.number) == cta_number
                    ]
                    if links:
                        progress = _task_group_progress_payload(
                            game, links, team=actor_team, user=actor_user,
                            anon_key=anon_key,
                            mode=game.get_current_mode(Attempt(time=timezone.now())),
                        )
                        card['is_solved'] = bool(
                            (progress.get(cta_number) or {}).get('is_fully_solved')
                        )
                        card['progress_embedded'] = True
                except Exception:
                    logger.exception('Hub task-group progress failed; using JSON fallback game=%s', game.id)
            by_id[game_id] = card

    from games.daily.timing.streak import daily_streaks_for_actor
    streak_kwargs = {'games': [card['game'] for card in by_id.values() if card.get('game')]}
    if request.user.is_authenticated:
        streak_kwargs['user'] = request.user
    else:
        streak_kwargs['anon_key'] = _anon_key_from_request(request)
    streaks = daily_streaks_for_actor(**streak_kwargs)
    for card in by_id.values():
        card['streak'] = streaks.get(str(card.get('id')), 0)

    daily = []
    for game_id in ONBOARDING_GAME_IDS:
        if game_id not in by_id:
            continue
        card = by_id[game_id]
        card['onboarding_badge'] = ONBOARDING_GAME_META[game_id]['badge']
        daily.append(card)
    from_desyatochki = [
        by_id[i] for i in HUB_FROM_DESYATOCHKI_SECTION_IDS if i in by_id
    ]
    daily_grid = [by_id[i] for i in HUB_DAILY_SECTION_IDS if i in by_id]
    return {
        'daily_hub_cards': daily,
        'daily_hub_primary_card': None,
        'daily_hub_secondary_cards': [],
        'daily_hub_grid_cards': daily_grid,
        'from_desyatochki_hub_cards': from_desyatochki,
        'hub_section_cards': daily_grid + from_desyatochki,
    }


def _onboarding_game_cards(daily_hub_cards):
    """One recommended game and two alternatives for the focused /start/ page."""
    from games.sections.paths import section_last_path

    cards_by_id = {card['id']: card for card in daily_hub_cards}
    cards = []
    for game_id in ONBOARDING_GAME_IDS:
        source = cards_by_id.get(game_id)
        if source is None:
            continue
        meta = ONBOARDING_GAME_META[game_id]
        cards.append({
            'id': game_id,
            'ph_icon': source.get('ph_icon', ''),
            'title': meta['title'],
            'analytics_game': meta['analytics_game'],
            'badge': meta['badge'],
            'description': meta['description'],
            'duration': meta['duration'],
            'cta_label': meta['cta_label'],
            # The stable /last/ route resolves the current published number at
            # request time and never hardcodes it in this landing page.
            'play_url': section_last_path(game_id),
        })
    return cards


def _onboarding_starter_salad_url(daily_hub_cards):
    """Configured beginner Salad when public, otherwise the stable latest URL."""
    from games.sections.paths import section_last_path, section_play_path

    fallback = section_last_path(WORD_SALAD_GAME_ID)
    starter_number = str(
        getattr(settings, 'ONBOARDING_STARTER_SALAD_ID', '') or ''
    ).strip()
    if not starter_number:
        return fallback
    salad_card = next(
        (card for card in daily_hub_cards if card['id'] == WORD_SALAD_GAME_ID),
        None,
    )
    published_numbers = {
        str(number) for number in salad_card.get('published_numbers', set())
    } if salad_card else set()
    if not salad_card or starter_number not in published_numbers:
        return fallback
    return section_play_path(WORD_SALAD_GAME_ID, starter_number)


def _has_gameplay_history(**actor):
    """Modern analytics rows plus legacy interactions predating game_start."""
    if PlayerStartedGame.objects.filter(**actor).exists():
        return True
    if PlayerCompletedGame.objects.filter(**actor).exists():
        return True
    if Attempt.manager.filter(skip=False, **actor).exists():
        return True
    return HintAttempt.objects.filter(**actor).exists()


def _anonymous_gameplay_history(anon_key):
    anon_key = str(anon_key or '').strip()
    if not is_valid_anon_key(anon_key):
        return False
    actor = {
        'anon_key': anon_key,
        'team__isnull': True,
        'user__isnull': True,
    }
    return _has_gameplay_history(**actor)


def _is_first_time_player(request):
    """Whether this account/browser has no modern or legacy gameplay history."""
    if request.user.is_authenticated:
        # Do not require the actor columns to be exclusive here: some historic
        # team Attempts kept both the team and the submitting user.
        if _has_gameplay_history(user=request.user):
            return False
        # Registration and the optional anon-progress merge are separate
        # requests. Honour this browser's cookie in between so onboarding does
        # not flash back immediately after a player signs up.
        return not _anonymous_gameplay_history(browser_anon_key(request))

    return not _anonymous_gameplay_history(_anon_key_from_request(request))


def _get_ladder_game():
    return (
        Game.objects.filter(id=LADDER_GAME_ID, project_id=NEW_UI_SECTIONS_PROJECT)
        .first()
    )


def _sections_hub_url(game_id):
    """Canonical public URL for a sections-project game hub."""
    from games.sections.paths import section_hub_path
    return section_hub_path(game_id)


def _published_numbers(game):
    return {link.number for link in filter_published_links(_game_task_group_links(game), game)}


def _daily_archive_context(request, game, links, *, completed_numbers=(), status_by_key=None,
                           locked_keys=()):
    """Adapt section placements to the neutral reusable archive component."""
    meta = SECTION_HUB_META.get(game.id) or {}
    items = build_daily_archive_items(
        links,
        game_id=game.id,
        publish_at_for_number=lambda number: publish_at_for(game, number),
        href_for_number=lambda number: _play_url_for_task_group(game, number),
        timezone_name=MOSCOW,
    )
    return refresh_daily_archive_context(
        request, items=items, game_id=game.id,
        game_label=meta.get('archive_item_label') or meta.get('title') or game.name,
        completed_keys={str(number) for number in completed_numbers},
        status_by_key=status_by_key,
        locked_keys=locked_keys,
    )


def _add_daily_completion_statuses(request, game, rows, links):
    """Add the small flame state shown next to a solved daily task."""
    from games.daily.timing.streak import daily_completion_statuses_for_actor

    _team, user, anon_key = _resolve_game_page_actor(request, 'personal')
    try:
        statuses = daily_completion_statuses_for_actor(
            game=game, links=links, user=user, anon_key=anon_key,
        )
    except Exception:
        logger.exception('Daily completion statuses failed for game=%s', game.id)
        statuses = {}
    by_number = {}
    for link in links:
        game_link = link[1] if isinstance(link, tuple) else link
        number = link[0] if isinstance(link, tuple) else link.number
        status = statuses.get(game_link.task_group_id)
        if status:
            by_number[str(number)] = status
    for row in rows:
        row['daily_completion_status'] = by_number.get(str(row.get('number')))
    return rows


def _ladder_published_numbers(game):
    return _published_numbers(game)


def _ladder_latest_play_url(game):
    """URL of the newest published ladder, or None if none exist yet."""
    numbers = [
        int(n) for n in _published_numbers(game)
        if str(n).isdigit() and scheduled_number_is_public(game, n)
    ]
    if not numbers:
        return None
    return _play_url_for_task_group(game, max(numbers))


def _ladder_task_group_rows(
    task_groups,
    game,
    *,
    today_number=None,
    today_prefix='Сегодня',
    item_label='Лесенка',
):
    task_groups = list(task_groups)
    from games.daily.difficulty.core import get_cached_game_difficulties
    difficulties = get_cached_game_difficulties(task_groups)
    endpoints_by_task_group = {}
    if game.id == LADDER_GAME_ID:
        task_group_ids = [p.task_group_id for p in task_groups]
        raddle_tasks = (
            Task.objects.filter(
                task_group_id__in=task_group_ids,
                task_type='raddle',
            )
            .visible()
            .only('id', 'task_group_id', 'task_type', 'checker_data', 'answer')
            .order_by('task_group_id', 'id')
        )
        for task in raddle_tasks:
            if task.task_group_id in endpoints_by_task_group:
                continue
            parsed = parse_raddle_data(task)
            if not parsed or not parsed['words']:
                continue
            first_word = parsed['words'][0]
            last_word = parsed['words'][-1]
            if first_word and last_word:
                endpoints_by_task_group[task.task_group_id] = (first_word, last_word)

    salad_meta_by_task_group = {}
    if game.id == WORD_SALAD_GAME_ID:
        salad_tasks = (
            Task.objects.filter(
                task_group_id__in=[p.task_group_id for p in task_groups],
                task_type='word_salad',
            )
            .visible()
            .only('id', 'task_group_id', 'checker_data', 'text')
            .order_by('task_group_id', 'id')
        )
        for task in salad_tasks:
            if task.task_group_id in salad_meta_by_task_group:
                continue
            salad_meta_by_task_group[task.task_group_id] = word_salad_archive_card_meta(task)

    rows = []
    for p in task_groups:
        is_today = today_number is not None and str(p.number) == str(today_number)
        title = '{} №{}'.format(item_label, p.number)
        published_at = publish_at_for(game, p.number)
        endpoints = endpoints_by_task_group.get(p.task_group_id)
        rows.append({
            'task_group': p.task_group,
            'game': game,
            'number': p.number,
            'publish_date': published_at.astimezone(MOSCOW).date() if published_at else None,
            'n_tasks': p.n_tasks,
            'n_solved': None,
            'play_url': _play_url_for_task_group(game, p.number),
            'results_url': _task_group_results_url(game, p.number),
            'is_fully_solved': False,
            'is_today': is_today,
            'row_class': 'new-task--today' if is_today else '',
            'title': title,
            'progress_text': None,
            'raddle_start_word': endpoints[0] if endpoints else None,
            'raddle_end_word': endpoints[1] if endpoints else None,
            'salad_meta': salad_meta_by_task_group.get(p.task_group_id),
            'difficulty': difficulties.get(p.pk),
        })
    return rows


def _folder_by_slug(slug):
    for f in NEW_UI_FOLDERS:
        if f['slug'] == slug:
            return f
    return None


__all__ = [
    'NEW_UI_FOLDERS',
    '_SECTION_NAV_ORDER',
    'get_section_games',
    '_hub_section_task_group_links',
    '_build_hub_section_cards',
    '_onboarding_game_cards',
    '_onboarding_starter_salad_url',
    '_has_gameplay_history',
    '_anonymous_gameplay_history',
    '_is_first_time_player',
    '_get_ladder_game',
    '_sections_hub_url',
    '_published_numbers',
    '_daily_archive_context',
    '_add_daily_completion_statuses',
    '_ladder_published_numbers',
    '_ladder_latest_play_url',
    '_ladder_task_group_rows',
    '_folder_by_slug',
]
