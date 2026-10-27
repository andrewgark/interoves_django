"""Цензурки: daily numbered play + random hash play + APIs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.core.cache import caches
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from games.club.access.archive import has_club_access
from games.console.http.access import user_has_support_access
from games.daily.catalog.censorly import (
    CENSORLY_GAME_ID,
    MOSCOW,
    censorly_publish_at,
    current_censorly_number,
    get_censorly_hub_context,
    is_censorly_number_published,
    visible_censorly_links,
)
from games.daily.completion.completion_coordinator import complete_logical_game
from games.daily.page.archive import (
    links_for_archive_month,
    prepare_daily_archive,
    refresh_daily_archive_context,
)
from games.daily.page.context import build_daily_lifecycle_context, daily_statistics_url
from games.daily.registry import get_daily_game
from games.daily.timing import GameplayTimingSession, TimingSessionNotActive
from games.daily.timing.transitions import next_daily_content_transition_for_game
from games.identity import has_profile
from games.models import Attempt, BugReport, Game, GameTaskGroup, RandomCensorlyGame, Task
from games.ops.analytics.events import (
    PlayerCompletedGame,
    is_task_completion_state,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.ops.telegram.links import task_play_url
from games.puzzles.censorly import CensorlyMoveRefusal, CensorlyPuzzle
from games.puzzles.censorly.article import reports as censorly_word_reports
from games.puzzles.censorly.article.admin import taxonomy as censorly_taxonomy
from games.puzzles.censorly.lexical.match.core import fold
from games.puzzles.censorly.play import (
    CENSORLY_HINT_PENALTY,
    _actor_filters,
    attach_solve_meta,
    get_play_state,
    get_task_for_number,
    hub_progress_for_actor,
)
from games.sections.hub import (
    create_game_link_context,
    onboarding_followup_context,
    section_format_credit_context,
)
from games.sections.paths import section_hub_path, section_play_path, section_replay_path, section_results_path
from games.site.http.auth_observability import record_gameplay_attempt_created
from games.site.http.middleware.request_timing import timing_phase
from games.site.http.request_util import get_public_task_or_404
from games.site.pages.game.progress import (
    _archive_nav_target,
    _neighbors_by_pk,
    _task_group_page_nav_context,
)
from games.site.session.gameplay_context import (
    context_error_response,
    issue_gameplay_context,
    validate_gameplay_context,
)
from games.site.session.replay import StaleReplayError, _official_exists, active_replay, replay_for_request
from games.site.views.daily.common import (
    get_section_game,
    last_redirect,
    meta_context,
    preview_denied,
    published_numbers,
    resolve_actor,
    share_host,
    today_redirect,
    with_meta_bar,
)
from games.site.views.daily.hub import can_access_daily_game
from games.site.views.daily.timing import daily_timing_page_context
from games.site.views.game.context import (
    game_from_request_for_task,
    unpublished_scheduled_task_response,
)
from games.task_group.titles import daily_edition_title, task_display_name

CENSORLY_WORD_REPORT_LIMIT = 20
CENSORLY_WORD_REPORT_WINDOW_SECONDS = 10 * 60
censorly_report_cache = caches['censorly_api']


def _get_game():
    return get_section_game(CENSORLY_GAME_ID)


def _published_numbers(game):
    return published_numbers(game, digits_only=True)


def _article_json(payload, *, status=200):
    """JSON for responses that carry the masked article.

    A full article is several thousand token objects; Django's default
    ``ensure_ascii`` writes each Cyrillic letter as a 6-byte ``\\uXXXX``
    escape and pads every separator with a space, which adds up on a
    several-hundred-kilobyte article.
    """
    return JsonResponse(
        payload,
        status=status,
        json_dumps_params={'ensure_ascii': False, 'separators': (',', ':')},
    )


def _compact_play_response(result):
    """Drop the unchanged article from ordinary guess and hint responses.

    The initial page/state response already contains the full masked article.
    Only newly revealed public tokens need to cross the wire after a guess or
    a hint.
    Winning responses stay full so the final reveal remains a simple, robust
    compatibility path.
    """
    if result.get('status') in ('won', 'already_won'):
        return result
    # Errors carry no article; a delta would only copy their fields into
    # the client's game state.
    if 'title_tokens' not in result and 'body_tokens' not in result:
        return result
    title_tokens = result.get('title_tokens') or []
    body_tokens = result.get('body_tokens') or []
    revealed_ids = {str(token_id) for token_id in result.get('newly_revealed') or []}
    token_updates = [
        token
        for token in [*title_tokens, *body_tokens]
        if str(token.get('id')) in revealed_ids
    ]
    compact = dict(result)
    compact.pop('title_tokens', None)
    compact.pop('body_tokens', None)
    compact['state_delta'] = {
        key: value
        for key, value in result.items()
        if key not in ('title_tokens', 'body_tokens')
    }
    compact['state_delta']['token_updates'] = token_updates
    return compact


def _may_preview(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False):
        return True
    return user_has_support_access(user)


def _may_open_unpublished(user) -> bool:
    """Staff or support may preview numbered slots before their release."""
    return _may_preview(user)


def _may_access_censorly(request, game) -> bool:
    """Use the same public preview permission as other daily games."""
    return can_access_daily_game(request, game)


def _censorly_timing_enabled(game, load_meta, user, replay_slot):
    """Match the timer visibility rules used by the Censorly play page."""
    if replay_slot is not None or not load_meta:
        return False
    link = load_meta.get('accepted_link')
    if link is None:
        return False
    is_random = bool(load_meta.get('is_random'))
    is_share_link = bool(load_meta.get('is_share_link'))
    is_offer = (
        not is_random
        and not is_share_link
        and not is_censorly_number_published(game, link.number)
        and not _may_open_unpublished(user)
    )
    return not is_offer


def _daily_timing_inactive_response(exc):
    timing_snapshot = dict(exc.timing_snapshot)
    timing_snapshot['resume_required'] = True
    return JsonResponse({
        'status': 'error',
        'error': 'Задание на паузе. Нажмите «Продолжить» и повторите действие.',
        'error_code': 'timer_not_running',
        'daily_timing': timing_snapshot,
    }, status=409)


def _json_body(request) -> dict:
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _play_location(load_meta, number):
    """(number shown in shares, play page path) of the loaded puzzle."""
    if load_meta:
        return load_meta.get('play_number'), load_meta.get('play_path')
    return number, section_play_path(CENSORLY_GAME_ID, number)


def _meta_placement(load_meta):
    return load_meta.get('accepted_link') if not load_meta.get('is_random') else None


def _load_random(share_hash: str):
    row = (
        RandomCensorlyGame.objects.filter(share_hash=str(share_hash))
        .select_related('task_group')
        .first()
    )
    if row is None:
        raise LookupError('not_found')
    task = Task.objects.filter(task_group_id=row.task_group_id, number='1').first()
    if task is None:
        raise LookupError('task_not_found')
    link = GameTaskGroup.objects.filter(
        game_id=CENSORLY_GAME_ID,
        task_group_id=row.task_group_id,
    ).first()
    return row, task, link


def _load_visible_task(request, number, *, json_mode=True, random_hash=None):
    game = _get_game()
    if not game:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)

    if random_hash:
        try:
            row, task, link = _load_random(random_hash)
        except LookupError:
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        if link is not None and link.is_deferred:
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        denied = preview_denied(request, game)
        if denied is not None:
            return None, None, None, denied
        meta = {
            'play_number': row.share_hash,
            'play_path': f'/censorly/r/{row.share_hash}/',
            'accepted_link': link,
            'schedule_number': None,
            'is_random': True,
            'random_row': row,
        }
        return game, task, meta, None

    denied = preview_denied(request, game)
    if denied is not None:
        return None, None, None, denied
    try:
        n = int(number)
    except (TypeError, ValueError):
        # Numbered scheduled tasks may also be opened through their share hash.
        from games.daily.page.placement import resolve_accessible_daily_placement
        from games.models import RandomCensorlyGame

        shared_link = resolve_accessible_daily_placement(
            game, number, user=request.user,
        )
        if shared_link is not None and not RandomCensorlyGame.objects.filter(
            task_group_id=shared_link.task_group_id,
        ).exists():
            task = Task.objects.filter(
                task_group_id=shared_link.task_group_id, number='1',
            ).first()
            if task is None:
                return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
            denied = preview_denied(request, game)
            if denied is not None:
                return None, None, None, denied
            meta = {
                'play_number': shared_link.share_hash,
                'play_path': section_play_path(CENSORLY_GAME_ID, shared_link.share_hash),
                'accepted_link': shared_link,
                'schedule_number': int(shared_link.number),
                'is_random': False,
                'is_share_link': True,
                'random_row': None,
            }
            return game, task, meta, None
        # Permanent random games use share_hash as GameTaskGroup.number.
        return _load_visible_task(request, None, json_mode=json_mode, random_hash=str(number))
    from games.daily.page.placement import resolve_accessible_daily_placement

    if resolve_accessible_daily_placement(game, n, user=request.user) is None:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not published'}, status=404)
    from games.club.access.archive import reject_if_club_archive_blocked

    locked = reject_if_club_archive_blocked(request, game, number=n, json_mode=json_mode)
    if locked is not None:
        return None, None, None, locked
    try:
        link, task = get_task_for_number(game, n)
    except LookupError:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    meta = {
        'play_number': n,
        'play_path': section_play_path(CENSORLY_GAME_ID, n),
        'accepted_link': link,
        'schedule_number': n,
        'is_random': False,
        'is_share_link': False,
        'random_row': None,
    }
    return game, task, meta, None


def censorly_hub_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    if not _may_access_censorly(request, game):
        raise Http404()
    team = request.user.profile.team_on if has_profile(request.user) else None
    hub = get_censorly_hub_context(game, published_numbers=_published_numbers(game))
    # The public archive shows released numbered slots only. Staff and support
    # can still open unreleased numbers through their direct URLs; random games
    # use a separate path below and are unaffected.
    schedule_links = []
    for link in GameTaskGroup.objects.filter(game=game).select_related('task_group'):
        if link.is_deferred:
            continue
        if not str(link.number).isdigit():
            continue
        if is_censorly_number_published(game, int(link.number)):
            schedule_links.append(link)
    schedule_links.sort(key=lambda link: int(link.number), reverse=True)
    archive_items, archive_context = prepare_daily_archive(
        request,
        schedule_links,
        game_id=CENSORLY_GAME_ID,
        publish_at_for_number=lambda number: censorly_publish_at(game, number),
        href_for_number=lambda number: section_play_path(CENSORLY_GAME_ID, number),
        timezone_name=MOSCOW,
        number_for_link=lambda link: int(link.number),
        game_label='Цензурка',
        calendar_id='daily-archive-censorly',
    )
    selected = archive_context.get('daily_archive_selected')
    visible_schedule_links = links_for_archive_month(
        schedule_links, selected,
        publish_at_for_number=lambda number: censorly_publish_at(game, number),
        number_for_link=lambda link: int(link.number), timezone_name=MOSCOW,
    )
    tasks_by_group = {
        task.task_group_id: task
        for task in Task.objects.filter(
            task_group_id__in=[link.task_group_id for link in schedule_links],
            number='1',
        )
    }
    user, anon_key = resolve_actor(request)
    progress = hub_progress_for_actor(
        game=game,
        numbers_and_tasks=[
            (int(link.number), tasks_by_group[link.task_group_id])
            for link in schedule_links
            if link.task_group_id in tasks_by_group
        ],
        user=user,
        anon_key=anon_key,
    )
    rows = []
    for link in visible_schedule_links:
        number = int(link.number)
        published_at = censorly_publish_at(game, number)
        rows.append({
            'number': number,
            'placement_id': link.pk,
            'publish_date': published_at.astimezone(MOSCOW).date() if published_at else None,
            'name': f'Цензурка №{number}',
            'play_url': section_play_path(CENSORLY_GAME_ID, number),
            'results_url': f'{section_play_path(CENSORLY_GAME_ID, number)}results/',
            'is_today': number == current_censorly_number(game),
            'is_solved': bool(progress.get(number, {}).get('is_solved')),
            'is_fully_solved': bool(progress.get(number, {}).get('is_solved')),
            'row_class': progress.get(number, {}).get('row_class', ''),
            'progress_meta': progress.get(number, {}).get('progress_meta', ''),
        })
    from games.site.pages.game.progress import _mark_locked_archive_rows
    _mark_locked_archive_rows(request, game, rows)
    from games.daily.difficulty.core import get_cached_game_difficulties
    difficulties = get_cached_game_difficulties(schedule_links)
    for row in rows:
        row['difficulty'] = difficulties.get(row.pop('placement_id'))
    from games.site.pages.hub.cards import _add_daily_completion_statuses
    _add_daily_completion_statuses(request, game, rows, schedule_links)
    archive_context = refresh_daily_archive_context(
        request, items=archive_items, game_label='Цензурка',
        game_id=CENSORLY_GAME_ID,
        completed_keys={str(row['number']) for row in rows if row['is_solved']},
        status_by_key={
            str(row['number']): row['row_class'].replace('new-task--', '')
            for row in rows if row['row_class']
        },
        locked_keys={row['number'] for row in rows if row.get('is_archive_locked')},
    )
    random_access = has_club_access(request.user)
    return render(request, 'site/censorly_hub.html', {
        'page_title': 'Цензурки',
        'random_button_template': 'site/partials/censorly_random_button.html',
        'daily_hub_play_url': hub['censorly_play_url'],
        'daily_hub_cta_label': hub['censorly_cta_label'],
        'daily_hub_is_today': hub['censorly_is_today'],
        'daily_hub_status': hub['censorly_status'],
        'daily_hub_coming_soon_text': 'Новая Цензурка — каждый день в полночь по Москве.',
        'rules_title': 'Цензурка',
        'rules_storage_key': 'section_rules_seen_censorly',
        'rules_body_template': 'site/_censorly_rules_body.html',
        'empty_text': 'Цензурки скоро появятся.',
        'create_link': create_game_link_context(CENSORLY_GAME_ID),
        'game': game,
        'rows': rows,
        'random_censorly_has_access': random_access,
        'censorly_taxonomy': censorly_taxonomy() if random_access else {},
        'support_url': '/support/censorly/',
        'show_sections_nav': True,
        'back_url': '/',
        'section_results_url': section_results_path(CENSORLY_GAME_ID),
        'can_see_results': game.has_access('see_results', team=team),
        **archive_context,
    })


@require_POST
def censorly_random_game(request):
    """Create or reuse a permanent random game for a Club resident."""
    if not has_club_access(request.user):
        return redirect('/subscription/')
    if _get_game() is None:
        raise Http404()
    from games.puzzles.censorly.random_game import (
        CensorlyPoolExhausted,
        CensorlyPoolFetchFailed,
        EmptyCensorlyPool,
        NoMatchingCensorlyArticles,
        get_or_create_random_game,
    )

    try:
        with timing_phase(request, 'create_random_censorly'):
            try:
                filters = json.loads(request.POST.get('filters') or '{}')
            except (TypeError, ValueError):
                filters = {}
            random_game = get_or_create_random_game(filters=filters if isinstance(filters, dict) else {})
    except EmptyCensorlyPool:
        messages.warning(
            request,
            'Пул случайных Цензурок пока пуст. Попробуйте ещё раз позже.',
        )
        return redirect('ui_censorly_hub')
    except NoMatchingCensorlyArticles:
        messages.info(request, 'По выбранным тегам статей нет. Попробуйте изменить фильтры.')
        return redirect('ui_censorly_hub')
    except CensorlyPoolFetchFailed:
        messages.warning(request, 'Не удалось загрузить статью из пула. Попробуйте ещё раз позже.')
        return redirect('ui_censorly_hub')
    except CensorlyPoolExhausted:
        messages.info(
            request,
            'Вы уже решили все статьи, подходящие под выбранные теги.',
        )
        return redirect('ui_censorly_hub')
    return redirect(f'/censorly/r/{random_game.share_hash}/')


def censorly_today_page(request):
    return today_redirect(request, CENSORLY_GAME_ID)


def censorly_last_page(request):
    return last_redirect(request, CENSORLY_GAME_ID, digits_only=True)


def _render_play(request, *, game, task, load_meta):
    play_number = load_meta.get('play_number')
    play_path = load_meta.get('play_path')
    link = load_meta.get('accepted_link')
    is_random = bool(load_meta.get('is_random'))
    schedule_number = load_meta.get('schedule_number')
    n = None if is_random else schedule_number

    user, anon_key = resolve_actor(request)
    replay_slot = active_replay(
        request=request, game=game, task_group=task.task_group,
        user=user, anon_key=anon_key,
    )
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=share_host(request),
        play_path=play_path,
        replay_slot=replay_slot,
    )
    pub_at = censorly_publish_at(game, n) if n is not None else None
    daily_publish_date = pub_at.date() if pub_at is not None else None

    prev_tg = None
    next_tg = None
    if link is not None and not is_random:
        visible_links = list(
            visible_censorly_links(
                GameTaskGroup.objects.filter(game=game),
                game,
            )
        )
        visible_links = [x for x in visible_links if str(x.number).isdigit()]
        prev_tg, next_tg = _neighbors_by_pk(visible_links, link)

    team = None
    if user is not None and has_profile(user):
        team = user.profile.team_on
    # Same rule as the gameplay endpoints, which reject an official move
    # with replay_required: a page that disagreed opened an official play
    # (and timer) that every request then refused.
    official_completed = _official_exists(
        game=game, task_group=task.task_group, user=user, anon_key=anon_key,
    )

    meta_ctx = meta_context(
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=link if not is_random else None,
    )
    edition = link if not is_random else None
    # Never put wiki_title here before win — it spoils the tab title.
    page_title = daily_edition_title(game, edition, play_number)
    bug_report_task_label = (
        task_display_name(game, task, placement=edition) if edition is not None else page_title
    )

    daily_lifecycle_context = build_daily_lifecycle_context(
        CENSORLY_GAME_ID,
        is_daily_single_task=True,
        placement_number=link.number if link is not None else play_number,
        placement_is_task_group=link is not None,
        number_is_public=(
            is_random
            or (link is not None and is_censorly_number_published(game, link.number))
        ),
        allow_unpublished=_may_open_unpublished(request.user),
        fallback_label='Цензурка',
        fallback_pager_label='Цензурками',
    )
    stats_enabled = bool(get_daily_game(CENSORLY_GAME_ID) and get_daily_game(CENSORLY_GAME_ID).capabilities.statistics)
    stats_number = play_number if is_random else (link.number if link else play_number)
    random_access = has_club_access(request.user)
    return render(request, 'site/censorly_play.html', {
        'game': game,
        'offer_share_hash': (
            play_number if is_random or (link is not None and load_meta.get('is_share_link')) else ''
        ),
        'number': play_number,
        'tg_number': play_number,
        'random_censorly_has_access': random_access,
        'censorly_taxonomy': censorly_taxonomy() if random_access else {},
        'link': link,
        'task': task,
        'page_title': page_title if not state.get('won') else (
            state.get('wiki_title') or page_title
        ),
        # The tab title switches to the article after a win; the heading keeps the number.
        'page_heading': page_title,
        'bug_report_task_label': bug_report_task_label,
        'daily_publish_date': daily_publish_date,
        'hide_daily_navigation': is_random,
        'live_next_transition_at': (
            next_daily_content_transition_for_game(game) if not is_random else None
        ),
        'section_results_url': section_results_path(CENSORLY_GAME_ID),
        'task_results_url': f'{play_path}results/',
        'can_see_results': not is_random and game.has_access('see_results', team=team),
        'daily_results_url': f'{play_path}results/',
        'daily_results_allowed': not is_random and game.has_access('see_results', team=team),
        'daily_results_label': 'Таблица результатов',
        'official_completed': official_completed,
        'replay_active': replay_slot is not None,
        'replay_completed': bool(replay_slot and replay_slot.status == 'completed'),
        'replay_url': section_replay_path(CENSORLY_GAME_ID, play_number),
        'replay_exit_url': section_replay_path(CENSORLY_GAME_ID, play_number).rstrip('/') + '/exit/',
        **section_format_credit_context(CENSORLY_GAME_ID),
        **daily_lifecycle_context,
        # After lifecycle unpack so we don't get overwritten.
        'daily_statistics_url': daily_statistics_url(
            CENSORLY_GAME_ID,
            stats_number,
            enabled=stats_enabled,
        ),
        'show_sections_nav': True,
        'back_url': section_hub_path(CENSORLY_GAME_ID),
        'bootstrap': {
            **state,
            'guess_url': f'{play_path}guess/',
            'state_url': f'{play_path}state/',
            'hint_url': f'{play_path}hint/',
            'word_report_url': reverse('ui_censorly_word_report', args=[task.id]),
        },
        'guess_url': f'{play_path}guess/',
        'state_url': f'{play_path}state/',
        'hint_url': f'{play_path}hint/',
        'word_report_url': reverse('ui_censorly_word_report', args=[task.id]),
        'censorly_hint_penalty': CENSORLY_HINT_PENALTY,
        'anon_key': anon_key if user is None else '',
        'is_authenticated': bool(user),
        'gameplay_context_token': issue_gameplay_context(
            task=task, game=game, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        ),
        'prev_task_group_url': _archive_nav_target(
            request, game, prev_tg,
            section_play_path(CENSORLY_GAME_ID, prev_tg.number) if prev_tg else None,
        )[0],
        'next_task_group_url': _archive_nav_target(
            request, game, next_tg,
            section_play_path(CENSORLY_GAME_ID, next_tg.number) if next_tg else None,
        )[0],
        'prev_task_group_locked': _archive_nav_target(
            request, game, prev_tg,
            section_play_path(CENSORLY_GAME_ID, prev_tg.number) if prev_tg else None,
        )[1],
        'next_task_group_locked': _archive_nav_target(
            request, game, next_tg,
            section_play_path(CENSORLY_GAME_ID, next_tg.number) if next_tg else None,
        )[1],
        **(onboarding_followup_context(CENSORLY_GAME_ID) if not is_random else {}),
        **meta_ctx,
        **_task_group_page_nav_context(game, prev_tg=prev_tg, next_tg=next_tg),
        **daily_timing_page_context(
            request,
            game,
            link,
            user=user,
            anon_key=anon_key,
            play_mode='personal',
            is_offer=(
                not is_random
                and not bool(load_meta.get('is_share_link'))
                and (
                    link is not None
                    and not is_censorly_number_published(game, link.number)
                    and not _may_open_unpublished(request.user)
                )
            ),
            replay_slot=replay_slot,
            official_completed=official_completed,
            timing_url=(
                f'/censorly/r/{play_number}/timing/' if is_random
                else f'/censorly/{play_number}/timing/' if load_meta.get('is_share_link')
                else None
            ),
        ),
    })


def censorly_play_page(request, number=None, share_hash=None):
    with timing_phase(request, 'censorly_task_load'):
        game, task, load_meta, err = _load_visible_task(
            request, number, json_mode=False, random_hash=share_hash,
        )
    if err is not None:
        if getattr(err, 'status_code', None) == 403:
            return err
        raise Http404()
    if game is None or task is None:
        raise Http404()
    with timing_phase(request, 'censorly_page_render'):
        return _render_play(request, game=game, task=task, load_meta=load_meta)


@require_POST
def censorly_word_report(request, task_id):
    """Store a user report about one already revealed Censorly token."""
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None or game.id != CENSORLY_GAME_ID:
        raise Http404()
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()
    user, anon_key = resolve_actor(request)
    context_error = validate_gameplay_context(
        request, task=task, game=game, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error_response(context_error)
    if user is None and not anon_key:
        raise Http404()
    if not game.has_access(
        'read_googledoc', team=None, attempt=Attempt(time=timezone.now()),
    ):
        raise Http404()

    body = _json_body(request)
    reason = str(body.get('reason') or '').strip().upper()
    if reason not in {'FALSE_POSITIVE', 'FALSE_NEGATIVE', 'OTHER'}:
        return JsonResponse({'ok': False, 'error': 'Выберите причину жалобы.'}, status=400)
    target_surface = ' '.join(str(body.get('target_surface') or '').split())
    other_word = ' '.join(str(body.get('other_word') or '').split())
    comment = str(body.get('comment') or '').strip()
    if not target_surface or len(target_surface) > 128:
        return JsonResponse({'ok': False, 'error': 'Не удалось определить открытое слово.'}, status=400)
    if len(other_word) > 64 or len(comment) > 5000:
        return JsonResponse({'ok': False, 'error': 'Слишком длинное сообщение.'}, status=400)
    try:
        token_id = int(body.get('token_id'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Не удалось определить позицию слова.'}, status=400)
    state = get_play_state(game=game, task=task, user=user, anon_key=anon_key)
    token = next((item for item in [
        *(state.get('title_tokens') or []), *(state.get('body_tokens') or []),
    ] if int(item.get('id', -1)) == token_id), None)
    if not token or not token.get('revealed') or fold(token.get('text') or '') != fold(target_surface):
        return JsonResponse({'ok': False, 'error': 'Пожаловаться можно только на открытое слово.'}, status=400)

    if reason == 'FALSE_POSITIVE':
        # Use the recorded opening guess as the source of truth. The client
        # displays this value read-only, but reports can also be posted directly.
        for guess in state.get('guesses') or []:
            if not isinstance(guess, dict):
                continue
            if token_id in (guess.get('token_ids') or []):
                other_word = ' '.join(str(guess.get('word') or '').split())
                break
    if reason in {'FALSE_POSITIVE', 'FALSE_NEGATIVE'} and not other_word:
        return JsonResponse({'ok': False, 'error': 'Укажите второе слово.'}, status=400)

    actor_key = 'user:{}'.format(user.pk) if user is not None else 'anon:{}'.format(anon_key)
    now = int(time.time())
    bucket = now // CENSORLY_WORD_REPORT_WINDOW_SECONDS
    rate_key = 'censorly_word_report_v1:{}:{}'.format(actor_key, bucket)
    if censorly_report_cache.add(rate_key, 1, timeout=CENSORLY_WORD_REPORT_WINDOW_SECONDS):
        report_count = 1
    else:
        try:
            report_count = censorly_report_cache.incr(rate_key)
        except ValueError:
            censorly_report_cache.add(rate_key, 1, timeout=CENSORLY_WORD_REPORT_WINDOW_SECONDS)
            report_count = 1
    if report_count > CENSORLY_WORD_REPORT_LIMIT:
        return JsonResponse(
            {'ok': False, 'error': 'Слишком много жалоб. Попробуйте позже.'},
            status=429,
            headers={'Retry-After': str(CENSORLY_WORD_REPORT_WINDOW_SECONDS - now % CENSORLY_WORD_REPORT_WINDOW_SECONDS)},
        )

    expected = 'CLOSED' if reason == 'FALSE_POSITIVE' else 'OPEN' if reason == 'FALSE_NEGATIVE' else ''
    # The pair diagnostic is filled in by the integrations worker before the
    # admin is notified; computing it here would pin ~100 MB in this process.
    diagnostic = censorly_word_reports.initial_diagnostic(other_word)

    article = state.get('wiki_title') or getattr(task, 'answer', '') or ''
    reason_label = {
        'FALSE_POSITIVE': 'Лишнее открытие',
        'FALSE_NEGATIVE': 'Пропущенное открытие',
        'OTHER': 'Другая проблема',
    }[reason]
    lines = [
        'Жалоба на матчинг слов Цензурки',
        'Причина: {}'.format(reason_label),
        'Открытое слово: «{}»'.format(target_surface),
    ]
    if other_word:
        lines.append('Слово пользователя: «{}»'.format(other_word))
    if expected:
        lines.append('Ожидание: {}'.format(expected))
    if comment:
        lines.extend(['', comment])
    metadata = {
        'reason': reason,
        'expected': expected,
        'target_surface': target_surface,
        'target_normalized': fold(target_surface),
        'target_token_id': token_id,
        'target_lemma': token.get('lemma') or '',
        'target_is_title': bool(token.get('title_lemma')),
        'other_word': other_word,
        'comment': comment,
        'article': article,
        'game_id': game.id,
        'task_id': task.id,
        'referer': (request.META.get('HTTP_REFERER') or '')[:500],
        'deploy_version': getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unknown',
        'diagnostic': diagnostic,
    }
    try:
        from games.ops.cron.runtime import deployment_environment
        metadata['environment'] = deployment_environment()
    except Exception:
        metadata['environment'] = 'unknown'
    report = BugReport.objects.create(
        report_type=BugReport.REPORT_TYPE_CENSORLY_WORD_MATCH,
        task=task,
        game=game,
        user=user,
        anon_key=anon_key,
        text='\n'.join(lines),
        page_url=task_play_url(game, task)[:500],
        metadata=metadata,
        status=BugReport.STATUS_PENDING,
    )
    if diagnostic is not None:
        censorly_word_reports.request_completion(report.pk)
    return JsonResponse({'ok': True, 'report_id': report.pk})


# Moves the game accepted: they start the game and keep the timer lease alive.
_ACCEPTED_MOVE_STATUSES = frozenset({'hit', 'miss', 'already_open', 'hint', 'won'})
# Moves that changed the actor's state: other tabs and devices must sync.
_STATE_CHANGING_STATUSES = frozenset({'hit', 'miss', 'hint', 'won'})
# Moves that can change the meta bar (hint count, solve result). Rendering it
# performs several unrelated DB reads, so other moves skip it.
_META_BAR_STATUSES = frozenset({'hint', 'won', 'already_won'})


@dataclass(frozen=True)
class _MoveRequest:
    """A guess or hint request that passed access, context and replay checks."""

    request: object
    game: Game
    task: Task
    load_meta: dict
    number: object
    body: dict
    user: object
    anon_key: object
    replay_slot: object
    timing: GameplayTimingSession | None

    @property
    def play_location(self):
        return _play_location(self.load_meta, self.number)

    def play_kwargs(self) -> dict:
        """Arguments shared by ``apply_guess`` and ``apply_hint``."""
        request, user = self.request, self.user
        number, play_path = self.play_location
        return {
            'game': self.game,
            'task': self.task,
            'user': user,
            'anon_key': self.anon_key,
            'number': number,
            'share_host': share_host(request),
            'play_path': play_path,
            'submitted_by_profile_id': request.user.pk if has_profile(request.user) else None,
            'attempt_created_callback': lambda attempt: record_gameplay_attempt_created(
                request, attempt=attempt,
                actor_kind='user' if user is not None else 'anon',
            ),
            'timing': self.timing,
            'replay_slot': self.replay_slot,
            'timing_request': request,
        }


def _start_move(request, number, share_hash):
    """Return (move request, None), or (None, response) when the move is refused."""
    game, task, load_meta, err = _load_visible_task(request, number, random_hash=share_hash)
    if err is not None:
        return None, err
    body = _json_body(request)
    user, anon_key = resolve_actor(request, body=body)
    context_error = validate_gameplay_context(
        request, task=task, game=game, user=user, anon_key=anon_key,
    )
    if context_error:
        return None, context_error_response(context_error)
    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task.task_group,
            user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return None, JsonResponse({'status': 'error', 'error': 'stale_replay', 'reload_required': True})
    if replay_slot is None and _official_exists(
        game=game, task_group=task.task_group, user=user, anon_key=anon_key,
    ):
        return None, JsonResponse({'status': 'error', 'error': 'replay_required', 'reload_required': True})
    timing = None
    if _censorly_timing_enabled(game, load_meta, request.user, replay_slot):
        timing = GameplayTimingSession(
            game=game,
            task_group=task.task_group,
            user=user,
            anon_key=anon_key,
            session_id=body.get('timing_session_id'),
            claimed_ms=body.get('timing_unconfirmed_ms'),
        )
    return _MoveRequest(
        request=request, game=game, task=task, load_meta=load_meta, number=number,
        body=body, user=user, anon_key=anon_key, replay_slot=replay_slot, timing=timing,
    ), None


def _run_move(move: _MoveRequest, phase: str, apply_move):
    try:
        with timing_phase(move.request, phase):
            result = apply_move(**move.play_kwargs())
    except TimingSessionNotActive as exc:
        return _daily_timing_inactive_response(exc)

    status = result.get('status')
    if move.timing is not None and status in _ACCEPTED_MOVE_STATUSES:
        snap = move.timing.note_activity()
        if snap is not None:
            result['daily_timing'] = snap
    result = _finish_completion(move, result)
    if status in _STATE_CHANGING_STATUSES:
        from games.daily.projection.realtime import publish_actor_state_change
        publish_actor_state_change(
            task=move.task, game=move.game, user=move.user, replay_slot=move.replay_slot,
            reason='censorly.state_changed',
        )
    if status in _META_BAR_STATUSES:
        with timing_phase(move.request, 'render_meta'):
            result = with_meta_bar(
                result, move.request, game=move.game, task=move.task,
                user=move.user, anon_key=move.anon_key,
                placement=_meta_placement(move.load_meta),
            )
    return _article_json(
        _compact_play_response(result), status=400 if status == 'error' else 200,
    )


def _finish_completion(move: _MoveRequest, result):
    request, game, task = move.request, move.game, move.task
    user, anon_key, replay_slot = move.user, move.anon_key, move.replay_slot
    analytics_user = request.user if request.user.is_authenticated else None
    analytics_events = []
    if replay_slot is None and result.get('status') in _ACCEPTED_MOVE_STATUSES:
        analytics_events.extend(register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=analytics_user,
            task=task,
            game=game,
        ))
    state_blob = json.dumps({
        'guesses': result.get('guesses') or [],
        'won': result.get('won'),
        'hints_taken': result.get('hints') or result.get('hints_taken') or 0,
        'revealed_lemmas': [
            g.get('lemma') for g in (result.get('guesses') or []) if isinstance(g, dict)
        ],
    })
    if is_task_completion_state(task, state_blob) and is_task_group_complete(
        task_group=task.task_group,
        game=game,
        user=user,
        anon_key=anon_key,
        mode=game.get_current_mode(Attempt(time=timezone.now())),
        replay_slot=replay_slot,
    ):
        completion = complete_logical_game(
            actor={'team': None, 'user': user, 'anon_key': anon_key},
            game=game,
            task_group=task.task_group,
            task=task,
            replay_slot=replay_slot,
            run_id=getattr(request, 'interoves_replay_run_id', None),
            analytics_user=analytics_user,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=game.get_current_mode(Attempt(time=timezone.now())),
            source='censorly',
        )
        if completion is not None:
            if completion.get('timing'):
                result['daily_timing'] = completion['timing']
                # The share was built before completion froze the time; build
                # it again so it shows the same duration as the results.
                if result.get('won'):
                    number, play_path = move.play_location
                    attach_solve_meta(
                        result, game=game, task=task, number=number,
                        actor=_actor_filters(user=user, anon_key=anon_key, replay_slot=replay_slot),
                        host=share_host(request), play_path=play_path,
                    )
            analytics_events.extend(publish_completion_analytics(
                record=completion['record'],
                created=completion['created'],
                user=user,
                anon_key=anon_key,
                analytics_user=analytics_user,
                game=game,
                task_group=task.task_group,
            ))
        if replay_slot is None:
            result['replay_available'] = True
    if analytics_events:
        result['analytics_events'] = analytics_events
    return result


@require_http_methods(['GET'])
def censorly_state(request, number=None, share_hash=None):
    game, task, load_meta, err = _load_visible_task(
        request, number, random_hash=share_hash,
    )
    if err is not None:
        return err
    user, anon_key = resolve_actor(request)
    play_number, play_path = _play_location(load_meta, number)
    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task.task_group,
            user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return JsonResponse({'status': 'error', 'error': 'stale_replay', 'reload_required': True})
    state = get_play_state(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=share_host(request),
        play_path=play_path,
        replay_slot=replay_slot,
    )
    payload = with_meta_bar(
        {'status': 'ok', **state},
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=_meta_placement(load_meta),
    )
    return _article_json(payload)


@require_POST
def censorly_guess(request, number=None, share_hash=None):
    move, refused = _start_move(request, number, share_hash)
    if refused is not None:
        return refused
    word_source = (move.body, request.POST)
    return _run_move(
        move, 'apply_guess',
        lambda **kwargs: CensorlyPuzzle.apply_posted_guess(*word_source, **kwargs),
    )


@require_POST
def censorly_hint(request, number=None, share_hash=None):
    move, refused = _start_move(request, number, share_hash)
    if refused is not None:
        return refused
    prepared = CensorlyPuzzle.posted_hint_token(move.body)
    if isinstance(prepared, CensorlyMoveRefusal):
        return JsonResponse({'status': 'error', 'error': prepared.error}, status=400)
    return _run_move(
        move, 'apply_hint',
        lambda **kwargs: CensorlyPuzzle.apply_posted_hint(move.body, **kwargs),
    )
