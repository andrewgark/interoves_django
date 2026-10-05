"""Цензурки: daily numbered play + random hash play + APIs."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from games.analytics import (
    PlayerCompletedGame,
    is_task_completion_state,
    is_task_group_complete,
    publish_completion_analytics,
    register_started_game,
)
from games.analytics_identity import gameplay_anon_key
from games.censorly.play import (
    CENSORLY_HINT_PENALTY,
    apply_guess,
    apply_hint,
    get_play_state,
    get_task_for_number,
    hint_count,
    load_state,
    ru_hint_word,
)
from games.censorly_daily import (
    CENSORLY_GAME_ID,
    censorly_publish_at,
    current_censorly_number,
    filter_published_censorly_links,
    get_censorly_hub_context,
    is_censorly_number_published,
    visible_censorly_links,
)
from games.completion_coordinator import complete_logical_game
from games.club_access import has_club_access
from games.daily.page_context import build_daily_lifecycle_context, daily_statistics_url
from games.daily.registry import get_daily_game
from games.daily_transitions import next_daily_content_transition_for_game
from games.gameplay_context import (
    context_error_response,
    issue_gameplay_context,
    validate_gameplay_context,
)
from games.middleware.request_timing import timing_phase
from games.models import Attempt, ChainTaskState, Game, GameTaskGroup, Like, RandomCensorlyGame, Task
from games.section_hub import onboarding_followup_context, section_format_credit_context
from games.replay import active_replay, replay_for_request, StaleReplayError, _official_exists
from games.section_paths import section_hub_path, section_play_path, section_replay_path, section_results_path
from games.support.access import user_has_support_access
from games.task_titles import task_display_name, task_group_page_title
from games.views.daily_timing_views import daily_timing_page_context
from games.views.new_ui import (
    NEW_UI_SECTIONS_PROJECT,
    _archive_nav_target,
    _neighbors_by_pk,
    _task_group_page_nav_context,
)
from games.views.util import has_profile


def _share_host(request) -> str:
    return request.get_host() or 'interoves.com'


def _get_game():
    return Game.objects.filter(
        id=CENSORLY_GAME_ID,
        project_id=NEW_UI_SECTIONS_PROJECT,
    ).first()


def _may_preview(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False):
        return True
    return user_has_support_access(user)


def _censorly_is_public(game) -> bool:
    """Opt-in public launch via Game.tags['censorly_public']; soft-launch otherwise."""
    tags = game.tags if isinstance(game.tags, dict) else {}
    return bool(tags.get('censorly_public'))


def _may_open_unpublished(user) -> bool:
    """Staff or support may preview unpublished numbered slots during soft launch."""
    return _may_preview(user)


def _may_access_censorly(request, game) -> bool:
    """Soft launch: staff/support only. After censorly_public: normal see_game_preview."""
    if _may_preview(request.user):
        return True
    if not _censorly_is_public(game):
        return False
    team = None
    if has_profile(request.user):
        team = request.user.profile.team_on
    return bool(game.has_access('see_game_preview', team=team))


def _published_numbers(game):
    links = GameTaskGroup.objects.filter(game=game)
    return {
        link.number
        for link in filter_published_censorly_links(links, game)
        if str(link.number).isdigit()
    }


def _resolve_actor(request, *, body=None):
    if request.user.is_authenticated:
        return request.user, None
    anon_key = gameplay_anon_key(request)
    if anon_key:
        return None, str(anon_key)
    return None, None


def _hints_taken(*, game, task, user, anon_key) -> int:
    qs = ChainTaskState.objects.filter(
        task=task, game=game, game_mode='general', replay_slot__isnull=True,
    )
    if user is not None:
        qs = qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        qs = qs.filter(anon_key=str(anon_key), team__isnull=True, user__isnull=True)
    else:
        return 0
    row = qs.first()
    if row is None:
        return 0
    return hint_count(load_state(row.state))


def _meta_context(request, *, game, task, user, anon_key, placement=None):
    mode = game.get_current_mode(Attempt(time=timezone.now()))
    ai = Attempt.manager.get_attempts_info(
        team=None,
        task=task,
        mode=mode,
        user=user,
        anon_key=anon_key,
        game=game,
    )
    hints_n = _hints_taken(game=game, task=task, user=user, anon_key=anon_key)
    difficulty = None
    if placement is not None:
        from games.difficulty import get_game_difficulty
        difficulty = get_game_difficulty(placement)
    return {
        'game': game,
        'task': task,
        'ai': ai,
        'mode': mode,
        'base_max': task.get_points(),
        'wall_max_title': '',
        'task_ui': {
            'show_attempts': False,
            'show_answer': False,
            'alphabetty_hints_label': (
                f'{hints_n} {ru_hint_word(hints_n)}' if hints_n > 0 else ''
            ),
        },
        'is_daily_single_task': True,
        'difficulty': difficulty,
        'has_profile_user': has_profile(request.user),
        'user': request.user,
        'likes_meta_by_task_id': {
            task.id: {
                'likes': Like.manager.get_total_likes(task),
                'dislikes': Like.manager.get_total_dislikes(task),
                'liked': Like.manager.actor_has_like(
                    task, team=None, user=user, anon_key=anon_key,
                ),
                'disliked': Like.manager.actor_has_dislike(
                    task, team=None, user=user, anon_key=anon_key,
                ),
            },
        },
        'alphabetty_hints': hints_n,
        'alphabetty_hints_label': (
            f'{hints_n} {ru_hint_word(hints_n)}' if hints_n > 0 else ''
        ),
    }


def _meta_bar_html(request, *, game, task, user, anon_key, placement=None) -> str:
    return render_to_string(
        'new/task-content/task-meta-bar.html',
        _meta_context(
            request,
            game=game,
            task=task,
            user=user,
            anon_key=anon_key,
            placement=placement,
        ),
        request=request,
    )


def _with_meta_bar(payload, request, *, game, task, user, anon_key, placement=None):
    out = dict(payload)
    out['meta_bar_html'] = _meta_bar_html(
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=placement,
    )
    return out


def _preview_denied(request, game):
    if _may_access_censorly(request, game):
        return None
    return JsonResponse({'status': 'error', 'error': 'not found'}, status=404)


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
        denied = _preview_denied(request, game)
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

    denied = _preview_denied(request, game)
    if denied is not None:
        return None, None, None, denied
    try:
        n = int(number)
    except (TypeError, ValueError):
        # Permanent random games use share_hash as GameTaskGroup.number.
        return _load_visible_task(request, None, json_mode=json_mode, random_hash=str(number))
    if not is_censorly_number_published(game, n) and not _may_open_unpublished(request.user):
        return None, None, None, JsonResponse({'status': 'error', 'error': 'not published'}, status=404)
    from games.club_access import reject_if_club_archive_blocked

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
        'random_row': None,
    }
    return game, task, meta, None


def censorly_hub_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    if not _may_access_censorly(request, game):
        raise Http404()
    hub = get_censorly_hub_context(game, published_numbers=_published_numbers(game))
    random_rows = list(RandomCensorlyGame.objects.order_by('-created_at')[:40])
    # Soft-launch / staff: show all numeric slots. Public: only published.
    show_unpublished = _may_open_unpublished(request.user)
    schedule_links = []
    for link in GameTaskGroup.objects.filter(game=game).select_related('task_group'):
        if not str(link.number).isdigit():
            continue
        if show_unpublished or is_censorly_number_published(game, int(link.number)):
            schedule_links.append(link)
    schedule_links.sort(key=lambda link: int(link.number))
    return render(request, 'new/censorly_hub.html', {
        'page_title': 'Цензурки',
        'game': game,
        'rows': random_rows,
        'schedule_links': schedule_links,
        'random_censorly_has_access': has_club_access(request.user),
        'support_url': '/support/censorly/',
        'show_sections_nav': False,
        **hub,
    })


@require_POST
def censorly_random_game(request):
    """Create or reuse a permanent random game for a Club resident."""
    if not has_club_access(request.user):
        return redirect('/subscription/')
    if _get_game() is None:
        raise Http404()
    from games.censorly.random_game import (
        CensorlyPoolExhausted,
        EmptyCensorlyPool,
        get_or_create_random_game,
    )

    try:
        random_game = get_or_create_random_game()
    except EmptyCensorlyPool:
        messages.warning(
            request,
            'Пул случайных Цензурок пока пуст. Попробуйте ещё раз позже.',
        )
        return redirect('ui_censorly_hub')
    except CensorlyPoolExhausted:
        messages.info(
            request,
            'Все статьи из пула уже стали случайными Цензурками.',
        )
        return redirect('ui_censorly_hub')
    return redirect(f'/censorly/r/{random_game.share_hash}/')


def censorly_today_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    if not _may_access_censorly(request, game):
        raise Http404()
    n = current_censorly_number(game)
    if not n:
        return redirect('ui_censorly_hub')
    return redirect(section_play_path(CENSORLY_GAME_ID, n))


def censorly_last_page(request):
    game = _get_game()
    if not game:
        raise Http404()
    if not _may_access_censorly(request, game):
        raise Http404()
    ints = []
    for n in _published_numbers(game):
        try:
            ints.append(int(n))
        except (TypeError, ValueError):
            continue
    if not ints:
        return redirect('ui_censorly_hub')
    return redirect(section_play_path(CENSORLY_GAME_ID, max(ints)))


def _render_play(request, *, game, task, load_meta):
    play_number = load_meta.get('play_number')
    play_path = load_meta.get('play_path')
    link = load_meta.get('accepted_link')
    is_random = bool(load_meta.get('is_random'))
    schedule_number = load_meta.get('schedule_number')
    n = None if is_random else schedule_number

    user, anon_key = _resolve_actor(request)
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
        share_host=_share_host(request),
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
    actor_filter = (
        {'user': user, 'team__isnull': True, 'anon_key__isnull': True}
        if user is not None
        else {'anon_key': anon_key, 'team__isnull': True, 'user__isnull': True}
    )
    official_completed = PlayerCompletedGame.objects.filter(
        game=game,
        task_group=task.task_group,
        result=PlayerCompletedGame.RESULT_SOLVED,
        **actor_filter,
    ).exists() if anon_key or user else False

    meta_ctx = _meta_context(
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=link if not is_random else None,
    )
    if link is not None and not is_random:
        page_title = task_group_page_title(game, link)
        bug_report_task_label = task_display_name(game, task, placement=link)
    else:
        # Never put wiki_title here before win — it spoils the tab title.
        page_title = f'Цензурка · {play_number}'
        bug_report_task_label = page_title

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
        fallback_pager_label='цензурками',
    )
    stats_enabled = bool(get_daily_game(CENSORLY_GAME_ID) and get_daily_game(CENSORLY_GAME_ID).capabilities.statistics)
    stats_number = play_number if is_random else (link.number if link else play_number)
    return render(request, 'new/censorly_play.html', {
        'game': game,
        'number': play_number,
        'tg_number': play_number,
        'link': link,
        'task': task,
        'page_title': page_title if not state.get('won') else (
            state.get('wiki_title') or page_title
        ),
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
        'show_sections_nav': False,
        'back_url': section_hub_path(CENSORLY_GAME_ID),
        'back_label': 'К списку',
        'bootstrap': {
            **state,
            'guess_url': f'{play_path}guess/',
            'state_url': f'{play_path}state/',
            'hint_url': f'{play_path}hint/',
        },
        'guess_url': f'{play_path}guess/',
        'state_url': f'{play_path}state/',
        'hint_url': f'{play_path}hint/',
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
                is_random
                or (
                    link is not None
                    and not is_censorly_number_published(game, link.number)
                    and not _may_open_unpublished(request.user)
                )
            ),
            replay_slot=replay_slot,
            official_completed=official_completed,
        ),
    })


def censorly_play_page(request, number=None, share_hash=None):
    game, task, load_meta, err = _load_visible_task(
        request, number, json_mode=False, random_hash=share_hash,
    )
    if err is not None:
        if getattr(err, 'status_code', None) == 403:
            return err
        raise Http404()
    if game is None or task is None:
        raise Http404()
    return _render_play(request, game=game, task=task, load_meta=load_meta)


def _finish_completion(request, *, game, task, user, anon_key, result, replay_slot=None):
    analytics_events = []
    if replay_slot is None and result.get('status') in ('hit', 'miss', 'already_open', 'won', 'hint'):
        analytics_events.extend(register_started_game(
            user=user,
            anon_key=anon_key,
            analytics_user=request.user if request.user.is_authenticated else None,
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
            analytics_user=request.user if request.user.is_authenticated else None,
            result=PlayerCompletedGame.RESULT_SOLVED,
            mode=game.get_current_mode(Attempt(time=timezone.now())),
            source='censorly',
        )
        if completion is not None:
            if completion.get('timing'):
                result['daily_timing'] = completion['timing']
            analytics_events.extend(publish_completion_analytics(
                record=completion['record'],
                created=completion['created'],
                user=user,
                anon_key=anon_key,
                analytics_user=request.user if request.user.is_authenticated else None,
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
    user, anon_key = _resolve_actor(request)
    play_number = load_meta.get('play_number') if load_meta else number
    play_path = load_meta.get('play_path') if load_meta else section_play_path(CENSORLY_GAME_ID, number)
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
        share_host=_share_host(request),
        play_path=play_path,
        replay_slot=replay_slot,
    )
    payload = _with_meta_bar(
        {'status': 'ok', **state},
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=load_meta.get('accepted_link') if not load_meta.get('is_random') else None,
    )
    return JsonResponse(payload)


@require_POST
def censorly_guess(request, number=None, share_hash=None):
    game, task, load_meta, err = _load_visible_task(
        request, number, random_hash=share_hash,
    )
    if err is not None:
        return err
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError, UnicodeDecodeError):
        body = {}
    word = body.get('word') or body.get('guess') or request.POST.get('word') or ''
    user, anon_key = _resolve_actor(request, body=body)
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
    play_path = load_meta.get('play_path') if load_meta else section_play_path(CENSORLY_GAME_ID, number)
    with timing_phase(request, 'apply_guess'):
        result = apply_guess(
            game=game,
            task=task,
            word=word,
            user=user,
            anon_key=anon_key,
            number=play_number,
            share_host=_share_host(request),
            play_path=play_path,
            replay_slot=replay_slot,
        )
    result = _finish_completion(
        request, game=game, task=task, user=user, anon_key=anon_key, result=result,
        replay_slot=replay_slot,
    )
    with timing_phase(request, 'render_meta'):
        result = _with_meta_bar(
            result,
            request,
            game=game,
            task=task,
            user=user,
            anon_key=anon_key,
            placement=load_meta.get('accepted_link') if not load_meta.get('is_random') else None,
        )
    status_code = 200
    if result.get('status') == 'error':
        status_code = 400
    return JsonResponse(result, status=status_code)


@require_POST
def censorly_hint(request, number=None, share_hash=None):
    game, task, load_meta, err = _load_visible_task(
        request, number, random_hash=share_hash,
    )
    if err is not None:
        return err
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, TypeError, UnicodeDecodeError):
        body = {}
    try:
        token_id = int(body.get('token_id'))
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'error': 'Укажите token_id'}, status=400)
    user, anon_key = _resolve_actor(request, body=body)
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
    play_path = load_meta.get('play_path') if load_meta else section_play_path(CENSORLY_GAME_ID, number)
    result = apply_hint(
        game=game,
        task=task,
        token_id=token_id,
        user=user,
        anon_key=anon_key,
        number=play_number,
        share_host=_share_host(request),
        play_path=play_path,
        replay_slot=replay_slot,
    )
    result = _finish_completion(
        request, game=game, task=task, user=user, anon_key=anon_key, result=result,
        replay_slot=replay_slot,
    )
    result = _with_meta_bar(
        result,
        request,
        game=game,
        task=task,
        user=user,
        anon_key=anon_key,
        placement=load_meta.get('accepted_link') if not load_meta.get('is_random') else None,
    )
    status_code = 200
    if result.get('status') == 'error':
        status_code = 400
    return JsonResponse(result, status=status_code)
