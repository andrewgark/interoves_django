"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.daily.catalog.week_task import WEEK_TASK_GAME_ID
from games.daily.ladder.tutorial.ladder import (
    LADDER_TUTORIAL_TITLE,
    is_new_ladder_player,
    ladder_tutorial_payload,
    ladder_tutorial_url,
    reset_ladder_tutorial_progress,
    with_ladder_tutorial_link,
)
from games.daily.page.context import (
    build_daily_lifecycle_context,
)
from games.daily.page.section import (
    MOSCOW,
    WORD_SALAD_GAME_ID,
    is_scheduled_game,
    publish_at_for,
    scheduled_number_is_public,
    uses_daily_play_layout,
    visible_links,
)
from games.daily.page.share.authorship import is_author_auto_completion_active
from games.daily.registry import get_daily_game
from games.daily.timing.transitions import (
    next_daily_content_transition_for_game,
)
from games.identity import has_profile, has_team
from games.models import (
    Attempt,
    AudioManager,
    Game,
    GameTaskGroup,
    ImageManager,
    PlayerCompletedGame,
)
from games.results.share import share_host_from_request
from games.sections.hub import (
    SECTION_HUB_META,
    get_source_desyatka_context,
    onboarding_followup_context,
    section_format_credit_context,
    section_nav_title,
)
from games.sections.placement_share import may_open_unpublished_number
from games.sections.week_task_pool import source_play_path_from_tags, source_summary_from_tags
from games.site.pages import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    PALINDROMES_GAME_ID,
    SECTION_RULES_GAME_IDS,
    _age_gate_context,
    _anon_key_from_request,
    _get_play_mode,
    _project_urls_context,
    _root_project_game_redirect,
    _section_tutorial_html_for_game,
    _task_group_live_state_url,
)
from games.site.pages.game.access import _maybe_registration_or_announce_response
from games.site.pages.game.progress import (
    _archive_nav_target,
    _game_task_group_links,
    _neighbors_by_pk,
    _play_url_for_task_group,
    _task_group_page_nav_context,
    _task_group_replay_exit_url,
    _task_group_replay_url,
    _task_group_results_url,
)
from games.site.pages.hub.cards import _sections_hub_url
from games.site.pages.results.compute import _public_exclusion_notice
from games.site.session.play_mode import effective_play_mode, personal_play_mode_locked
from games.site.session.replay import _official_exists, active_replay
from games.task_group.task_group_context import build_task_group_task_context_dicts
from games.task_group.titles import task_group_page_title

logger = logging.getLogger('games.views.new_ui')

@never_cache
def daily_statistics(request, game_id, number):
    """Spoiler-safe statistics endpoint: completed participants only."""
    definition = get_daily_game(game_id)
    if definition is None or not definition.capabilities.statistics:
        raise Http404()
    game = get_object_or_404(Game, id=game_id, project_id=NEW_UI_SECTIONS_PROJECT)
    from games.daily.page.placement import (
        is_random_daily_placement,
        resolve_accessible_daily_placement,
    )
    from games.sections.placement_share import is_share_hash_segment

    segment_is_share_hash = is_share_hash_segment(str(number))
    placement = resolve_accessible_daily_placement(game, number, user=request.user)
    if segment_is_share_hash:
        if placement is None or not is_random_daily_placement(placement):
            raise Http404()
    else:
        if placement is None:
            raise Http404()
    actor = {}
    anon_key = None
    if request.user.is_authenticated:
        actor = {'user': request.user, 'team__isnull': True, 'anon_key__isnull': True}
    else:
        anon_key = _anon_key_from_request(request)
        if not anon_key:
            return JsonResponse({'error': 'not_completed'}, status=403)
        actor = {'anon_key': str(anon_key), 'team__isnull': True, 'user__isnull': True}
    if not PlayerCompletedGame.objects.filter(
        game=game, task_group=placement.task_group,
        result=PlayerCompletedGame.RESULT_SOLVED, **actor,
    ).exists():
        return JsonResponse({'error': 'not_completed'}, status=403)
    from games.daily.timing.statistics import build_daily_statistics
    published_at = None
    if not segment_is_share_hash:
        from games.daily.page.section import publish_at_for
        published_at = publish_at_for(game, placement.number)
    payload = build_daily_statistics(
        game, placement.task_group, published_at=published_at,
    )
    from games.daily.timing import (
        TIMING_VERSION_ACTIVE,
        canonical_elapsed_seconds,
        lookup_timing,
    )
    timing_row = lookup_timing(
        game=game,
        task_group=placement.task_group,
        user=request.user if request.user.is_authenticated else None,
        anon_key=anon_key,
    )
    legacy_attempts = None
    if timing_row is None or int(timing_row.timing_version or 0) < TIMING_VERSION_ACTIVE:
        legacy_actor = (
            {'user': request.user, 'team__isnull': True, 'anon_key__isnull': True}
            if request.user.is_authenticated
            else {'anon_key': str(anon_key), 'user__isnull': True, 'team__isnull': True}
        )
        legacy_attempts = list(Attempt.manager.filter(
            game=game,
            task__task_group=placement.task_group,
            skip=False,
            replay_slot__isnull=True,
            **legacy_actor,
        ).only('time'))
    personal_time_seconds = canonical_elapsed_seconds(
        game=game,
        task_group=placement.task_group,
        user=request.user if request.user.is_authenticated else None,
        anon_key=anon_key,
        attempts=legacy_attempts,
        timing_row=timing_row,
    )
    from games.daily.timing.statistics import (
        RANKING_KEY,
        personal_attempt_count,
        personal_censorly_perfect,
        personal_in_cohort,
        personal_statistics,
    )
    actor_key = (
        ('user', request.user.id) if request.user.is_authenticated
        else ('anon', str(anon_key))
    )
    ranking = payload.get(RANKING_KEY) or {}
    with_attempts = 'median_attempts' in (payload.get('summary') or {})
    ranked_maps = [ranking.get('times') or {}]
    if with_attempts:
        ranked_maps.append(ranking.get('attempts') or {})
    in_cohort = None
    if any(actor_key not in values for values in ranked_maps):
        # Either outside the public cohort or newer than a cached generation.
        in_cohort = personal_in_cohort(
            game, placement.task_group, actor_key=actor_key, published_at=published_at,
        )
    personal_attempts = None
    personal_perfect = False
    if with_attempts and actor_key not in (ranking.get('attempts') or {}):
        personal_attempts = personal_attempt_count(
            game, placement.task_group, actor_key=actor_key,
        )
        if in_cohort and payload.get('kind') == 'censorly':
            personal_perfect = personal_censorly_perfect(
                game, placement.task_group, actor_key=actor_key, attempts=personal_attempts,
            )
    payload = {
        **personal_statistics(
            payload,
            actor_key=actor_key,
            personal_time_seconds=personal_time_seconds,
            personal_attempts=personal_attempts,
            personal_perfect=personal_perfect,
            in_cohort=in_cohort,
        ),
        'personal_time_seconds': personal_time_seconds,
    }
    # A counted player missing from the cohort figures: let the client ask again.
    # The time map may lack an old solve without timing, so it alone decides
    # only when there is no attempts map and the player has a time.
    if in_cohort and (
        actor_key not in ranked_maps[-1] if with_attempts
        else personal_time_seconds is not None and actor_key not in ranked_maps[0]
    ):
        payload['_stale'] = True
    notice = _public_exclusion_notice(
        request, game, placement.task_group, surface='statistics',
    )
    if notice:
        payload = {**payload, 'exclusion_notice': notice}
    return JsonResponse(payload)


def task_group_page(
    request, game_id, task_group_number, ladder_tutorial_task=None, ladder_tutorial_return_to=None,
):
    """Play page of one task group.

    ``ladder_tutorial_task`` renders the tutorial Ladder (games/tutorials) on
    this same page, the way a draft ladder offer is rendered;
    ``ladder_tutorial_return_to`` is the Ladder the player came from.
    """
    game = get_object_or_404(
        Game.objects.select_related('section_default_rules'),
        id=game_id,
    )
    from games.sections.paths import section_play_path
    project_redirect = _root_project_game_redirect(
        request, game, '{}/'.format(task_group_number),
    )
    if project_redirect is not None:
        return project_redirect
    if game.project_id != NEW_UI_SECTIONS_PROJECT:
        from games.club.access.archive import reject_if_club_archive_blocked
        locked = reject_if_club_archive_blocked(request, game, number=task_group_number)
        if locked is not None:
            return locked

    # Обычные игры: до старта / без регистрации — карточка анонса, не 404.
    if game.project_id != NEW_UI_SECTIONS_PROJECT:
        gate = _maybe_registration_or_announce_response(
            request,
            game,
            back_url='/games/',
            show_sections_nav=True,
        )
        if gate is not None:
            return gate

    play_mode, play_mode_key = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    if ladder_tutorial_task is not None:
        # The tutorial is always a personal board, never a team's.
        play_mode = 'personal'
    anon_key = None

    if not request.user.is_authenticated:
        if personal_play_mode_locked(game, user=request.user):
            from urllib.parse import quote
            return redirect('/accounts/login/?next={}'.format(quote(request.get_full_path())))
        # До логина разрешаем только личный режим (не в турнире).
        play_mode = 'personal'
        anon_key = _anon_key_from_request(request)
    else:
        if play_mode == 'personal' and not has_profile(request.user):
            raise Http404()

    team = None
    user = None
    if play_mode == 'team':
        if not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on if has_team(request.user) else None
    else:
        user = request.user if request.user.is_authenticated else None

    # Для игр-разделов (project sections) хотим давать доступ всегда, без привязки к start_time.
    ladder_offer = None
    salad_offer = None
    share_placement = None
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        preview_team = None
        if has_profile(request.user):
            preview_team = request.user.profile.team_on
        if not game.has_access('see_game_preview', team=preview_team):
            raise Http404()
        from games.daily.page.placement import resolve_accessible_daily_placement
        from games.sections.placement_share import (
            is_share_hash_segment,
        )
        share_placement = None
        segment = str(task_group_number)
        if ladder_tutorial_task is not None:
            pass
        elif game_id == LADDER_GAME_ID and is_share_hash_segment(segment):
            from games.club.offers.ladder import can_access_offer_hash, get_offer_by_share_hash
            ladder_offer = get_offer_by_share_hash(segment)
            if ladder_offer is None:
                share_placement = resolve_accessible_daily_placement(
                    game, segment, user=request.user,
                )
                if share_placement is None:
                    raise Http404()
            elif not can_access_offer_hash(ladder_offer, request.user):
                raise Http404()
        elif game_id == WORD_SALAD_GAME_ID and is_share_hash_segment(segment):
            from games.club.offers.salad import (
                can_access_offer_hash as can_access_salad_offer_hash,
            )
            from games.club.offers.salad import (
                get_offer_by_share_hash as get_salad_offer_by_share_hash,
            )
            salad_offer = get_salad_offer_by_share_hash(segment)
            if salad_offer is None:
                share_placement = resolve_accessible_daily_placement(
                    game, segment, user=request.user,
                )
                if share_placement is None:
                    raise Http404()
            elif not can_access_salad_offer_hash(salad_offer, request.user):
                raise Http404()
        elif is_scheduled_game(game_id) and is_share_hash_segment(segment):
            share_placement = resolve_accessible_daily_placement(
                game, segment, user=request.user,
            )
            if share_placement is None:
                raise Http404()
        elif (
            is_scheduled_game(game_id)
            and resolve_accessible_daily_placement(
                game, task_group_number, user=request.user,
            ) is None
        ):
            raise Http404()
        if (
            ladder_offer is None and salad_offer is None and share_placement is None
            and ladder_tutorial_task is None
        ):
            from games.club.access.archive import reject_if_club_archive_blocked

            locked = reject_if_club_archive_blocked(
                request, game, number=task_group_number,
            )
            if locked is not None:
                return locked
    else:
        if play_mode == 'team':
            if not game.has_access('play', team=team):
                raise Http404()
        else:
            if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
                raise Http404()
            if not game.is_playable:
                raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    draft_offer = ladder_offer or salad_offer
    if ladder_tutorial_task is not None:
        from types import SimpleNamespace
        reset_ladder_tutorial_progress(
            ladder_tutorial_task, team=team, user=user, anon_key=anon_key,
        )
        # Behaves as an unaccepted draft offer: no archive neighbours, replay,
        # official release or results table.
        draft_offer = SimpleNamespace(accepted_link_id=None, share_hash='', user_id=None)
        placement = SimpleNamespace(
            pk=None,
            number=str(task_group_number),
            name=LADDER_TUTORIAL_TITLE,
            task_group=ladder_tutorial_task.task_group,
        )
    elif ladder_offer is not None:
        from types import SimpleNamespace
        if ladder_offer.accepted_link_id:
            placement = (
                GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
                .filter(pk=ladder_offer.accepted_link_id)
                .first()
            )
            if placement is None:
                raise Http404()
        else:
            placement = SimpleNamespace(
                pk=None,
                number=ladder_offer.share_hash,
                name=(ladder_offer.author or 'Лесенка').strip() or 'Лесенка',
                task_group=ladder_offer.task_group,
            )
    elif salad_offer is not None:
        from types import SimpleNamespace
        if salad_offer.accepted_link_id:
            placement = (
                GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
                .filter(pk=salad_offer.accepted_link_id)
                .first()
            )
            if placement is None:
                raise Http404()
        else:
            if not salad_offer.task_group_id:
                raise Http404()
            theme = (salad_offer.theme or '').strip()
            placement = SimpleNamespace(
                pk=None,
                number=salad_offer.share_hash,
                name=theme or 'Салатик',
                task_group=salad_offer.task_group,
            )
    elif share_placement is not None:
        placement = share_placement
    else:
        placement = (
            GameTaskGroup.objects.select_related('task_group', 'task_group__rules')
            .filter(game=game, number=str(task_group_number))
            .first()
        )
        if not placement:
            fallback = GameTaskGroup.nearest_by_number(game, task_group_number)
            if fallback:
                return redirect(_play_url_for_task_group(game, fallback.number))
            raise Http404()
    # A parked slot is not a public daily. Staff preview it, and the player
    # who submitted the offer can still open its share link.
    owns_deferred_offer = (
        request.user.is_authenticated
        and draft_offer is not None
        and getattr(draft_offer, 'accepted_link_id', None)
        and draft_offer.user_id == request.user.id
    )
    if (
        getattr(placement, 'is_deferred', False)
        and not may_open_unpublished_number(request.user)
        and not owns_deferred_offer
    ):
        raise Http404()
    task_group = placement.task_group
    if ladder_tutorial_task is not None or (game.id == LADDER_GAME_ID and ladder_offer is not None) or (
        game.id == WORD_SALAD_GAME_ID and salad_offer is not None
    ):
        prev_tg, next_tg = None, None
    elif is_scheduled_game(game.id):
        published_nav = list(visible_links(_game_task_group_links(game), game))
        prev_tg, next_tg = _neighbors_by_pk(published_nav, placement)
    else:
        prev_tg, next_tg = GameTaskGroup.prev_next_for(game, placement)
    replay_slot = None
    # An unaccepted draft has no schedule placement, so replay cannot bind.
    # An accepted offer is that placement: the public URL is still the share
    # hash, and dropping the session here hid the exit control while every
    # move came back stale_replay.
    draft_has_no_placement = (
        draft_offer is not None and not draft_offer.accepted_link_id
    )
    if not draft_has_no_placement and isinstance(placement, GameTaskGroup):
        replay_slot = active_replay(
            request=request,
            game=game,
            task_group=task_group,
            team=team,
            user=user,
            anon_key=anon_key,
        )
    # Same rule as the gameplay endpoints, which reject an official move
    # with replay_required: a page that disagreed opened an official play
    # (and timer) that every request then refused.
    official_completed = _official_exists(
        game=game, task_group=task_group, team=team, user=user, anon_key=anon_key,
    )
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    section_rules_type = game.id if game.id in SECTION_RULES_GAME_IDS else None
    section_tutorial_html = _section_tutorial_html_for_game(game)
    show_palindrome_rules = game.id == PALINDROMES_GAME_ID
    if game.project_id == NEW_UI_SECTIONS_PROJECT:
        tg_rules = placement.task_group.rules
        if tg_rules and (tg_rules.html or '').strip():
            section_rules_type = None
            section_tutorial_html = None
            show_palindrome_rules = False
    ctx_dicts = build_task_group_task_context_dicts(
        game, task_group, tasks, team, user, anon_key, mode,
        placement=placement if isinstance(placement, GameTaskGroup) else None,
        replay_slot=replay_slot, request=request,
    )
    week_task_source_line = None
    week_task_source_url = None
    if game.id == WEEK_TASK_GAME_ID:
        tags = task_group.tags or {}
        src = source_summary_from_tags(tags)
        des = src.get('desyatka_label') or src.get('game_id')
        if des:
            week_task_source_line = 'из {}'.format(des)
            week_task_source_url = source_play_path_from_tags(tags) or None
    source_desyatka = None
    if game.id in ('replacements', 'walls', 'palindromes'):
        source_desyatka = get_source_desyatka_context(task_group, team=team)
    from games.sections.paths import ladder_word_results_path
    is_daily_single_task = uses_daily_play_layout(game.id)
    daily_publish_date = None
    if draft_offer is None:
        pub_at = publish_at_for(game, placement.number)
        if pub_at is not None:
            daily_publish_date = pub_at.astimezone(MOSCOW).date()
    elif draft_offer.accepted_link_id:
        try:
            prod_n = int(draft_offer.accepted_link.number)
        except (TypeError, ValueError, AttributeError):
            prod_n = None
        if prod_n is not None:
            pub_at = publish_at_for(game, prod_n)
            if pub_at is not None:
                daily_publish_date = pub_at.astimezone(MOSCOW).date()
    author_auto_completed = is_author_auto_completion_active(
        user=request.user,
        task_group=task_group,
        play_mode=play_mode,
        is_daily_single_task=is_daily_single_task,
        is_official_release=(draft_offer is None and isinstance(placement, GameTaskGroup)),
        published_at=pub_at if draft_offer is None else None,
        now=timezone.now(),
    )
    if ladder_tutorial_task is not None:
        page_title = placement.name
    elif draft_offer is not None:
        if draft_offer.accepted_link_id and str(getattr(draft_offer.accepted_link, 'number', '')).isdigit():
            page_title = '{} №{}'.format(
                game.outside_name or game.name,
                draft_offer.accepted_link.number,
            )
        else:
            page_title = '{} · {}'.format(
                game.outside_name or game.name,
                placement.name or 'предложение',
            )
    elif is_daily_single_task:
        page_title = task_group_page_title(game, placement)
    else:
        page_title = task_group_page_title(game, placement)
    if draft_offer is not None and not isinstance(placement, GameTaskGroup):
        for ui in ctx_dicts['task_ui_by_task_id'].values():
            ui['display_name'] = page_title

    can_reset_offer = False
    offer_reset_url = None
    if draft_offer is not None and ladder_tutorial_task is None:
        # An unaccepted draft has no schedule placement, so a replay slot
        # cannot exist there; every player resets their own solution instead.
        if not draft_offer.accepted_link_id or (
            request.user.is_authenticated
            and (draft_offer.user_id == request.user.id or request.user.is_staff)
        ):
            can_reset_offer = True
            if ladder_offer is not None:
                offer_reset_url = reverse(
                    'ui_create_ladder_reset', kwargs={'offer_id': ladder_offer.pk},
                )
            elif salad_offer is not None:
                offer_reset_url = reverse(
                    'ui_create_salad_reset', kwargs={'offer_id': salad_offer.pk},
                )

    custom_daily_task = draft_offer is not None
    public_task_segment = (
        share_placement.share_hash if share_placement is not None
        else draft_offer.share_hash if draft_offer is not None and draft_offer.accepted_link_id
        else placement.number
    )
    offer_share_hash = (
        (getattr(placement, 'share_hash', '') or '').strip()
        or (getattr(draft_offer, 'share_hash', '') or '').strip()
    )

    placement_is_deferred = getattr(placement, 'is_deferred', False)
    # The share link of an accepted, already published release is the same
    # daily as its number. Time that solve. Drafts and parked slots stay untimed.
    published_offer_release = (
        draft_offer is not None
        and bool(getattr(draft_offer, 'accepted_link_id', None))
        and isinstance(placement, GameTaskGroup)
        and not placement_is_deferred
        and scheduled_number_is_public(game, placement.number)
    )
    anon_offer_login_hint = (
        ladder_tutorial_task is None
        and draft_offer is not None
        and not getattr(draft_offer, 'accepted_link_id', None)
        and not request.user.is_authenticated
    )

    if ladder_tutorial_task is not None or placement_is_deferred:
        ladder_results_url = None
    elif ladder_offer is not None:
        ladder_results_url = ladder_word_results_path(ladder_offer.share_hash)
    elif game.id == LADDER_GAME_ID:
        ladder_results_url = ladder_word_results_path(public_task_segment)
    else:
        ladder_results_url = None

    daily_lifecycle_context = build_daily_lifecycle_context(
        game.id,
        is_daily_single_task=is_daily_single_task,
        placement_number=placement.number,
        placement_is_task_group=isinstance(placement, GameTaskGroup),
        number_is_public=(
            not getattr(placement, 'is_deferred', False)
            and scheduled_number_is_public(game, placement.number)
        ),
        allow_unpublished=may_open_unpublished_number(request.user),
        fallback_label=section_nav_title(game.id) or None,
        fallback_pager_label=(
            'Лесенками' if game.id == LADDER_GAME_ID
            else 'Салатиками' if game.id == WORD_SALAD_GAME_ID
            else 'заданиями недели' if game.id == WEEK_TASK_GAME_ID
            else 'кругами'
        ),
    )
    daily_results_allowed = bool(
        ladder_results_url
        and game.has_access('see_results', mode='general', team=team)
    )

    back_url = (
        _sections_hub_url(game.id)
        if game.project_id == NEW_UI_SECTIONS_PROJECT
        else (
            reverse('ui_main_game', kwargs={'game_id': game.id})
            if game.project_id == NEW_UI_PROJECT
            else '/'
        )
    )
    if ladder_offer is not None and request.user.is_authenticated and ladder_offer.user_id == request.user.id:
        back_url = reverse('ui_create_ladder')
        back_label = 'Мои Лесенки'
    elif salad_offer is not None and request.user.is_authenticated and salad_offer.user_id == request.user.id:
        back_url = reverse('ui_create_salad')
        back_label = 'Мои Салатики'
    elif game.project_id not in (NEW_UI_SECTIONS_PROJECT, NEW_UI_PROJECT):
        back_label = 'Все игры'
    else:
        back_label = None
    if ladder_tutorial_return_to is not None:
        back_url = ladder_tutorial_return_to['url']
        back_label = 'Назад к Лесенке'

    # Ladder play pages link to the tutorial; a player who has never started
    # a Ladder is offered it right away.
    ladder_tutorial_entry_url = None
    ladder_tutorial_offer = False
    if game.id == LADDER_GAME_ID and ladder_tutorial_task is None and replay_slot is None:
        ladder_tutorial_entry_url = ladder_tutorial_url(request.path)
        ladder_tutorial_offer = is_new_ladder_player(
            user=request.user if request.user.is_authenticated else None,
            anon_key=anon_key,
        )
        section_tutorial_html = with_ladder_tutorial_link(section_tutorial_html, ladder_tutorial_entry_url)

    difficulty = None
    if draft_offer is None and isinstance(placement, GameTaskGroup):
        from games.daily.difficulty.core import get_game_difficulty
        difficulty = get_game_difficulty(placement)

    from games.site.views.daily.timing import daily_timing_page_context

    return render(request, 'ui/task_group.html', {
        'game': game,
        'task_group': task_group,
        'tasks': tasks,
        'attempts_info_by_task_id': ctx_dicts['attempts_info_by_task_id'],
        'client_specs_by_task_id': ctx_dicts['client_specs_by_task_id'],
        'offline_answer_task_ids': ctx_dicts['offline_answer_task_ids'],
        'replacements_lines_data': ctx_dicts['replacements_lines_data'],
        'html_forms_data': ctx_dicts['html_forms_data'],
        'word_salad_data': ctx_dicts['word_salad_data'],
        'raddle_data': ctx_dicts['raddle_data'],
        **{
            '{}_data'.format(context_key): data
            for context_key, data in ctx_dicts['daily_board_contexts'].items()
            if context_key not in ('word_salad', 'raddle')
        },
        'proportions_chips': ctx_dicts['proportions_chips'],
        'wall_max_points_meta_by_task_id': ctx_dicts['wall_max_points_meta_by_task_id'],
        'likes_meta_by_task_id': ctx_dicts['likes_meta_by_task_id'],
        'text_with_forms_data': ctx_dicts['text_with_forms_data'],
        'hidden_until_solved': ctx_dicts['hidden_until_solved'],
        'tg_heading_html': ctx_dicts['tg_heading_html'],
        'task_ui_by_task_id': ctx_dicts['task_ui_by_task_id'],
        'gameplay_context_tokens': ctx_dicts['gameplay_context_tokens'],
        'can_like': True,
        'has_profile_user': has_profile(request.user),
        'mode': mode,
        # The tournament clock still runs and is persisted, but its control is
        # intentionally not shown until the player returns to general results.
        'hide_task_group_timer_ui': mode == 'tournament',
        'replay_slot': replay_slot,
        'replay_active': replay_slot is not None,
        'replay_completed': bool(replay_slot and replay_slot.status == 'completed'),
        'official_completed': official_completed,
        'author_auto_completed': author_auto_completed,
        'play_mode': play_mode,
        'play_mode_project_id': game.project_id,
        'anon_key': anon_key,
        'team': team,
        'show_palindrome_rules': show_palindrome_rules,
        'section_rules_type': section_rules_type,
        'section_tutorial_html': section_tutorial_html,
        'prev_task_group_url': _archive_nav_target(
            request, game, prev_tg,
            _play_url_for_task_group(game, prev_tg.number) if prev_tg else None,
        )[0],
        'next_task_group_url': _archive_nav_target(
            request, game, next_tg,
            _play_url_for_task_group(game, next_tg.number) if next_tg else None,
        )[0],
        'prev_task_group_locked': _archive_nav_target(
            request, game, prev_tg,
            _play_url_for_task_group(game, prev_tg.number) if prev_tg else None,
        )[1],
        'next_task_group_locked': _archive_nav_target(
            request, game, next_tg,
            _play_url_for_task_group(game, next_tg.number) if next_tg else None,
        )[1],
        'task_group_results_url': (
            '' if ladder_tutorial_task is not None
            or placement_is_deferred
            or (salad_offer is not None and not salad_offer.accepted_link_id)
            else _task_group_results_url(game, public_task_segment)
        ),
        'task_group_live_state_url': _task_group_live_state_url(game),
        'replay_url': (
            None if ladder_tutorial_task is not None
            else _task_group_replay_url(game, public_task_segment)
        ),
        'replay_exit_url': (
            None if ladder_tutorial_task is not None
            else _task_group_replay_exit_url(game, public_task_segment)
        ),
        # Replay needs a schedule placement, which an unaccepted draft lacks.
        'replay_unavailable': draft_offer is not None and not draft_offer.accepted_link_id,
        'task_group_results_allowed': game.has_access('see_results', mode='general', team=team),
        'tg_number': placement.number,
        'tg_name': placement.name,
        'share_host': share_host_from_request(request),
        'section_tagline': (
            SECTION_HUB_META.get(game.id, {}).get('description') or ''
            if game.project_id == NEW_UI_SECTIONS_PROJECT
            else ''
        ),
        'week_task_source_line': week_task_source_line,
        'week_task_source_url': week_task_source_url,
        'source_desyatka': source_desyatka,
        'custom_daily_task': custom_daily_task,
        'offer_share_hash': offer_share_hash,
        'daily_publish_date': daily_publish_date,
        'difficulty': difficulty,
        'ladder_word_results_url': ladder_results_url,
        'daily_results_url': ladder_results_url,
        'daily_results_allowed': daily_results_allowed,
        'daily_results_label': 'Таблица результатов' if game.id == LADDER_GAME_ID else '',
        **daily_lifecycle_context,
        **section_format_credit_context(game.id),
        'ladder_offer': ladder_offer,
        'ladder_tutorial': (
            ladder_tutorial_payload(ladder_tutorial_task, ladder_tutorial_return_to)
            if ladder_tutorial_task is not None else None
        ),
        'ladder_tutorial_entry_url': ladder_tutorial_entry_url,
        'ladder_tutorial_offer': ladder_tutorial_offer,
        'custom_reset_enabled': can_reset_offer,
        'custom_reset_url': offer_reset_url,
        'anon_offer_login_hint': anon_offer_login_hint,
        'back_url': back_url,
        **_task_group_page_nav_context(
            game, prev_tg=prev_tg, next_tg=next_tg, back_label=back_label,
        ),
        'page_title': page_title,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        'lock_personal_play_mode': personal_play_mode_locked(game, user=request.user),
        'show_sections_nav': True,
        'live_next_transition_at': (
            None if draft_offer is not None
            else next_daily_content_transition_for_game(game)
        ),
        **(
            onboarding_followup_context(game.id)
            if draft_offer is None else {}
        ),
        **_project_urls_context(game.project_id),
        **_age_gate_context(
            game,
            task_group=task_group,
            back_url=back_url,
        ),
        **daily_timing_page_context(
            request,
            game,
            placement if isinstance(placement, GameTaskGroup) else None,
            team=team,
            user=user,
            anon_key=anon_key,
            play_mode=play_mode,
            is_offer=draft_offer is not None and not published_offer_release,
            replay_slot=replay_slot,
            # The author already has the official daily credit; do not start
            # a solving timer merely because they opened their own release.
            official_completed=official_completed or author_auto_completed,
            timing_url=(
                '{}timing/'.format(section_play_path(
                    game.id,
                    draft_offer.share_hash if published_offer_release else share_placement.share_hash,
                ))
                if published_offer_release or (
                    share_placement is not None and draft_offer is None
                )
                else None
            ),
        ),
    })


__all__ = [
    'daily_statistics',
    'task_group_page',
]

