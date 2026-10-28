"""Алфавитка: hub, play page, guess API, prefix expand."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from games.club.access.archive import has_club_access
from games.club.offers.alphabetty import can_access_offer_hash, get_offer_by_share_hash
from games.daily.catalog.alphabetty import (
    ALPHABETTY_GAME_ID,
    alphabetty_publish_at,
    is_alphabetty_number_published,
    visible_alphabetty_links,
)
from games.daily.completion.completion_coordinator import complete_logical_game
from games.daily.page.context import build_daily_lifecycle_context
from games.daily.timing.transitions import next_daily_content_transition_for_game
from games.identity import has_profile
from games.models import (
    Attempt,
    GameTaskGroup,
    Task,
)
from games.ops.analytics.events import (
    PlayerCompletedGame,
    is_task_completion_state,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.puzzles.alphabetty import AlphabettyPuzzle
from games.puzzles.alphabetty.core import build_prefix_level, normalize_word
from games.puzzles.alphabetty.play import (
    ALPHABETTY_HINT_PENALTY,
    get_play_state,
    get_task_for_number,
    hub_progress_for_actor,
)
from games.puzzles.alphabetty.suggestions import suggest_word
from games.sections.hub import (
    onboarding_followup_context,
    section_format_credit_context,
)
from games.sections.paths import section_hub_path, section_play_path, section_replay_path, section_results_path
from games.site.http.auth_observability import record_gameplay_attempt_created
from games.site.http.middleware.request_timing import timing_phase
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
from games.task_group.titles import daily_edition_title, task_display_name


def _get_game():
    return get_section_game(ALPHABETTY_GAME_ID)


def _published_numbers(game):
    return published_numbers(game)


def _resolve_round_task_group(game, *, number=None, task_group_id=None):
    qs = GameTaskGroup.objects.filter(game=game)
    if task_group_id is not None:
        return qs.filter(pk=task_group_id).select_related('task_group').first()
    if number is not None:
        return qs.filter(number=str(number)).select_related('task_group').first()
    return None


def alphabetty_hub_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    if not can_access_daily_game(request, game):
        raise Http404()
    from games.site.views.daily.common import daily_hub_context

    return render(request, 'site/alphabetty_hub.html', daily_hub_context(
        request,
        game,
        progress_for_actor=hub_progress_for_actor,
        page_title='Алфавитка',
        random_button_template='site/partials/alphabetty_random_button.html',
        coming_soon_text='Новая Алфавитка — каждый день в полночь по Москве.',
        rules_body_template='site/_alphabetty_rules_body.html',
        empty_text='Алфавитки скоро появятся.',
    ))


@require_POST
def alphabetty_random_game(request):
    """Create or reuse a permanent random game for a Club resident."""
    if not has_club_access(request.user):
        return redirect('/subscription/')
    if _get_game() is None:
        raise Http404()
    from games.puzzles.alphabetty.random_game import (
        EmptyRandomAlphabettyDictionary,
        RandomAlphabettyDictionaryExhausted,
        get_or_create_random_game,
    )

    try:
        random_game = get_or_create_random_game(game=_get_game())
    except EmptyRandomAlphabettyDictionary:
        messages.warning(
            request,
            'Словарь случайных алфавиток пока пуст. Попробуйте ещё раз позже.',
        )
        return redirect('new_alphabetty_hub')
    except RandomAlphabettyDictionaryExhausted:
        messages.info(
            request,
            'Все слова из словаря уже стали случайными Алфавитками.',
        )
        return redirect('new_alphabetty_hub')
    except LookupError:
        messages.error(
            request,
            'Не удалось создать случайную Алфавитку. Попробуйте ещё раз.',
        )
        return redirect('new_alphabetty_hub')
    return redirect(section_play_path(ALPHABETTY_GAME_ID, random_game.share_hash))


def alphabetty_today_page(request):
    return today_redirect(request, ALPHABETTY_GAME_ID, use_published_cta=True)


def alphabetty_last_page(request):
    """Редирект на последнюю опубликованную Алфавитку (как /ladder/last/)."""
    return last_redirect(request, ALPHABETTY_GAME_ID)


def alphabetty_play_page(request, number):
    game, task, load_meta, err = _load_visible_task(request, number, json_mode=False)
    if err is not None:
        if getattr(err, 'status_code', None) == 403:
            return err
        raise Http404()
    if game is None or task is None:
        raise Http404()
    from games.sections.placement_share import may_open_unpublished_number

    offer = load_meta.get('offer') if load_meta else None
    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    link = load_meta.get('accepted_link') if load_meta else None
    is_random = bool(load_meta.get('is_random')) if load_meta else False
    if offer is None:
        schedule_number = load_meta.get('schedule_number') if load_meta else None
        if is_random:
            n = None
        elif schedule_number is not None:
            n = schedule_number
        else:
            try:
                n = int(play_number)
            except (TypeError, ValueError):
                raise Http404()
    else:
        n = play_number

    user, anon_key = resolve_actor(request)
    replay_slot = active_replay(
        request=request, game=game, task_group=task.task_group,
        user=user, anon_key=anon_key,
    )
    # Не генерируем anon на сервере: иначе перетирается localStorage из base.html
    # и теряется прогресс. Клиент подтянет state через /state/ со своим ключом.
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
    pub_at = alphabetty_publish_at(game, n) if offer is None and n is not None else None
    daily_publish_date = pub_at.date() if pub_at is not None else None
    # Соседи только среди уже вышедших алфавиток (как у лесенок).
    prev_tg = None
    next_tg = None
    if offer is None and link is not None and not is_random:
        visible_links = list(
            visible_alphabetty_links(
                GameTaskGroup.objects.filter(game=game),
                game,
            )
        )
        prev_tg, next_tg = _neighbors_by_pk(visible_links, link)
    team = None
    if user is not None and has_profile(user):
        team = user.profile.team_on
    official_completed = False
    if offer is None:
        # Same rule as the gameplay endpoints (replay_required).
        official_completed = _official_exists(
            game=game, task_group=task.task_group, user=user, anon_key=anon_key,
        )
    meta_ctx = meta_context(
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=link if offer is None else None,
    )
    edition = link if offer is None else None
    page_title = daily_edition_title(game, edition, play_number)
    bug_report_task_label = (
        task_display_name(game, task, placement=edition) if edition is not None else page_title
    )
    daily_lifecycle_context = build_daily_lifecycle_context(
        ALPHABETTY_GAME_ID,
        is_daily_single_task=True,
        placement_number=link.number if link is not None else play_number,
        placement_is_task_group=link is not None,
        number_is_public=(
            is_random
            or (link is not None and is_alphabetty_number_published(game, link.number))
        ),
        allow_unpublished=may_open_unpublished_number(request.user),
        fallback_label='Алфавитка',
        fallback_pager_label='Алфавитками',
    )
    # Share links are public; only the author goes back to their own list.
    is_own_offer = (
        offer is not None and request.user.is_authenticated
        and offer.user_id == request.user.id
    )
    return render(request, 'site/alphabetty_play.html', {
        'game': game,
        'offer_share_hash': (
            offer.share_hash if offer is not None
            else link.share_hash if link is not None and str(play_number) == str(link.share_hash)
            else ''
        ),
        'number': play_number,
        'tg_number': play_number,
        'link': link,
        'task': task,
        'page_title': page_title,
        'bug_report_task_label': bug_report_task_label,
        'daily_publish_date': daily_publish_date,
        'hide_daily_navigation': is_random,
        'live_next_transition_at': (
            next_daily_content_transition_for_game(game) if offer is None else None
        ),
        'section_results_url': section_results_path(ALPHABETTY_GAME_ID),
        'task_results_url': f'{play_path}results/',
        'can_see_results': offer is None and game.has_access('see_results', team=team),
        'daily_results_url': f'{play_path}results/',
        'daily_results_allowed': offer is None and game.has_access('see_results', team=team),
        'daily_results_label': 'Таблица результатов',
        'official_completed': official_completed,
        'replay_active': replay_slot is not None,
        'replay_completed': bool(replay_slot and replay_slot.status == 'completed'),
        'replay_url': section_replay_path(ALPHABETTY_GAME_ID, play_number),
        'replay_exit_url': section_replay_path(ALPHABETTY_GAME_ID, play_number).rstrip('/') + '/exit/',
        **section_format_credit_context(ALPHABETTY_GAME_ID),
        **daily_lifecycle_context,
        'show_sections_nav': True,
        'back_url': (
            reverse('ui_create_alphabetty') if is_own_offer
            else section_hub_path(ALPHABETTY_GAME_ID)
        ),
        'bootstrap': state,
        'guess_url': f'{play_path}guess/',
        'state_url': f'{play_path}state/',
        'prefix_url': f'{play_path}prefix/',
        'hint_url': f'{play_path}hint/',
        'alphabetty_hint_penalty': ALPHABETTY_HINT_PENALTY,
        'suggest_url': f'{play_path}suggest/',
        'anon_key': anon_key if user is None else '',
        'is_authenticated': bool(user),
        'gameplay_context_token': issue_gameplay_context(
            task=task, game=game, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        ),
        'prev_task_group_url': _archive_nav_target(
            request, game, prev_tg,
            section_play_path(ALPHABETTY_GAME_ID, prev_tg.number) if prev_tg else None,
        )[0],
        'next_task_group_url': _archive_nav_target(
            request, game, next_tg,
            section_play_path(ALPHABETTY_GAME_ID, next_tg.number) if next_tg else None,
        )[0],
        'prev_task_group_locked': _archive_nav_target(
            request, game, prev_tg,
            section_play_path(ALPHABETTY_GAME_ID, prev_tg.number) if prev_tg else None,
        )[1],
        'next_task_group_locked': _archive_nav_target(
            request, game, next_tg,
            section_play_path(ALPHABETTY_GAME_ID, next_tg.number) if next_tg else None,
        )[1],
        **(onboarding_followup_context(ALPHABETTY_GAME_ID) if offer is None else {}),
        **meta_ctx,
        **_task_group_page_nav_context(
            game,
            prev_tg=prev_tg,
            next_tg=next_tg,
            back_label='Мои Алфавитки' if is_own_offer else None,
        ),
        **daily_timing_page_context(
            request,
            game,
            link,
            user=user,
            anon_key=anon_key,
            play_mode='personal',
            is_offer=(
                offer is not None
                or (
                    offer is None
                    and not is_random
                    and not load_meta.get('is_share_link')
                    and link is not None
                    and not is_alphabetty_number_published(game, link.number)
                    and not may_open_unpublished_number(request.user)
                )
            ),
            official_completed=official_completed,
            timing_url=(
                f'/alphabetty/{play_number}/timing/'
                if offer is None and load_meta.get('is_share_link')
                else None
            ),
        ),
    })

def _load_visible_task(request, number, *, json_mode=True):
    game = _get_game()
    if not game:
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
    from games.daily.page.placement import resolve_accessible_daily_placement
    from games.models import RandomAlphabettyGame
    from games.sections.placement_share import is_share_hash_segment

    offer = get_offer_by_share_hash(str(number))
    if offer is None and is_share_hash_segment(str(number)):
        share_placement = resolve_accessible_daily_placement(
            game, number, user=request.user,
        )
        if share_placement is not None:
            task = Task.objects.filter(
                task_group_id=share_placement.task_group_id, number='1',
            ).first()
            if task is None:
                return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
            is_random = RandomAlphabettyGame.objects.filter(
                task_group_id=share_placement.task_group_id,
            ).exists()
            meta = {
                'offer': None,
                'play_number': share_placement.share_hash,
                'play_path': section_play_path(ALPHABETTY_GAME_ID, share_placement.share_hash),
                'accepted_link': share_placement,
                'schedule_number': None if is_random else int(share_placement.number),
                'is_random': is_random,
                'is_share_link': True,
            }
            return game, task, meta, None
    if offer is not None:
        if not can_access_offer_hash(offer, request.user):
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        if offer.accepted_link_id and offer.accepted_link.is_deferred:
            is_author = (
                getattr(request.user, 'is_authenticated', False)
                and offer.user_id == request.user.id
            )
            if not is_author and not getattr(request.user, 'is_staff', False):
                return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
        if task is None:
            return None, None, None, JsonResponse({'status': 'error', 'error': 'not found'}, status=404)
        meta = {
            'offer': offer,
            'play_number': offer.share_hash,
            'play_path': offer.play_url(),
            'accepted_link': offer.accepted_link,
            'is_share_link': False,
        }
        return game, task, meta, None
    denied = preview_denied(request, game)
    if denied is not None:
        return None, None, None, denied
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None, None, None, JsonResponse({'status': 'error', 'error': 'bad number'}, status=400)
    placement = resolve_accessible_daily_placement(game, n, user=request.user)
    if placement is None:
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
        'offer': None,
        'play_number': n,
        'play_path': section_play_path(ALPHABETTY_GAME_ID, n),
        'accepted_link': link,
        'is_share_link': False,
    }
    return game, task, meta, None


@require_http_methods(['GET'])
def alphabetty_state(request, number):
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err
    user, anon_key = resolve_actor(request)
    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
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
    payload = with_meta_bar(
        {'status': 'ok', **state},
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=load_meta.get('accepted_link') if not load_meta.get('offer') else None,
    )
    response = JsonResponse(payload)
    return response


@require_POST
def alphabetty_guess(request, number):
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}
    user, anon_key = resolve_actor(request, body=body)

    context_error = validate_gameplay_context(
        request, task=task, game=game, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error_response(context_error)
    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task.task_group,
            user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return JsonResponse({'status': 'error', 'error': 'stale_replay', 'reload_required': True})
    if replay_slot is None and _official_exists(
        game=game, task_group=task.task_group, user=user, anon_key=anon_key,
    ):
        return JsonResponse({'status': 'error', 'error': 'replay_required', 'reload_required': True})

    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    with timing_phase(request, 'apply_guess'):
        result = AlphabettyPuzzle.apply_posted_guess(
            body,
            request.POST,
            game=game,
            task=task,
            user=user,
            anon_key=anon_key,
            number=play_number,
            share_host=share_host(request),
            play_path=play_path,
            submitted_by_profile_id=(request.user.pk if has_profile(request.user) else None),
            attempt_created_callback=lambda attempt: record_gameplay_attempt_created(
                request, attempt=attempt,
                actor_kind='user' if user is not None else 'anon',
            ),
            replay_slot=replay_slot,
        )
    analytics_events = []
    if replay_slot is None and result.get('status') in ('earlier', 'later', 'correct'):
        analytics_events.extend(register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        ))
    if is_task_completion_state(task, json.dumps({
        'guesses': result.get('guesses') or [],
        'won': result.get('won'),
        'hint_prefix': result.get('hint_prefix') or '',
        'hints_taken': result.get('hints') or 0,
    })) and is_task_group_complete(
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
            analytics_user=request.user if request.user.is_authenticated else None,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=game.get_current_mode(Attempt(time=timezone.now())),
            source='alphabetty',
        )
        if completion is not None:
            if completion['timing']:
                result['daily_timing'] = completion['timing']
            if replay_slot is None:
                result['replay_available'] = True
                analytics_events.extend(publish_completion_analytics(
                    record=completion['record'],
                    created=completion['created'],
                    user=user,
                    anon_key=anon_key,
                    analytics_user=request.user if request.user.is_authenticated else None,
                    game=game,
                    task_group=task.task_group,
                ))
    if analytics_events:
        result['analytics_events'] = analytics_events
    if result.get('status') in ('earlier', 'later', 'correct'):
        from games.daily.projection.realtime import publish_actor_state_change
        publish_actor_state_change(
            task=task, game=game, user=user, replay_slot=replay_slot,
            reason='alphabetty.state_changed',
        )
    with timing_phase(request, 'render_meta'):
        result = with_meta_bar(
            result,
            request,
            game=game,
            task=task,
            user=user,
            anon_key=anon_key,
            placement=load_meta.get('accepted_link') if not load_meta.get('offer') else None,
        )
    with timing_phase(request, 'serialize_response'):
        response = JsonResponse(result)
    return response


@require_http_methods(['GET', 'POST'])
def alphabetty_prefix(request, number):
    """Раскрыть уровень префиксов между текущими границами (или переданными lo/hi)."""
    game, _task, _load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return JsonResponse({'ok': False, 'error': 'not found'}, status=404)

    if request.method == 'POST':
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, TypeError):
            body = {}
    else:
        body = request.GET

    lo = normalize_word(body.get('lo') or '') or None
    hi = normalize_word(body.get('hi') or '') or None
    expand = normalize_word(body.get('prefix') or '')
    rows = build_prefix_level(lo, hi, expand_prefix=expand)
    return JsonResponse({'ok': True, 'rows': rows})


@require_POST
def alphabetty_hint(request, number):
    game, task, load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}

    user, anon_key = resolve_actor(request, body=body)

    context_error = validate_gameplay_context(
        request, task=task, game=game, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error_response(context_error)
    try:
        replay_slot = replay_for_request(
            request=request, game=game, task_group=task.task_group,
            user=user, anon_key=anon_key,
        )
    except StaleReplayError:
        return JsonResponse({'status': 'error', 'error': 'stale_replay', 'reload_required': True})
    if replay_slot is None and _official_exists(
        game=game, task_group=task.task_group, user=user, anon_key=anon_key,
    ):
        return JsonResponse({'status': 'error', 'error': 'replay_required', 'reload_required': True})

    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(ALPHABETTY_GAME_ID, number)
    result = AlphabettyPuzzle.apply_posted_hint(
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=share_host(request),
        play_path=play_path,
        replay_slot=replay_slot,
    )
    if result.get('status') == 'ok':
        from games.daily.projection.realtime import publish_actor_state_change
        publish_actor_state_change(
            task=task, game=game, user=user, replay_slot=replay_slot,
            reason='alphabetty.state_changed',
        )
        analytics_events = [] if replay_slot is not None else register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
            task=task,
            game=game,
        )
        if analytics_events:
            result['analytics_events'] = analytics_events
    result = with_meta_bar(
        result,
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=load_meta.get('accepted_link') if not load_meta.get('offer') else None,
    )
    response = JsonResponse(result)
    return response


@require_POST
def alphabetty_suggest(request, number):
    """Предложить слово в словарь (pending → модерация в админке)."""
    game, task, _load_meta, err = _load_visible_task(request, number)
    if err is not None:
        return err

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError):
        body = {}
    word = body.get('word') or request.POST.get('word') or ''
    user, anon_key = resolve_actor(request, body=body)

    context_error = validate_gameplay_context(
        request, task=task, game=game, user=user, anon_key=anon_key,
    )
    if context_error:
        return context_error_response(context_error)

    result = suggest_word(word, user=user, anon_key=anon_key)
    return JsonResponse(result)
