from django.shortcuts import render

from games.daily.page.context import daily_statistics_url
from games.identity import has_profile
from games.models import AudioManager, GameTaskGroup, ImageManager
from games.task_group.task_group_context import build_task_group_task_context_dicts


def _task_card_public_identity(game, task_group, slot):
    """Return the public number/hash used by result-share cards.

    User-created section games have no ``GameTaskGroup`` until an offer is
    accepted.  The normal slot lookup therefore used to fall back to ``0``
    while rendering the post-attempt partial.
    """
    if slot is not None:
        return slot.number, slot.name

    if game.id == 'ladder':
        from games.daily.ladder.tutorial.ladder import (
            LADDER_TUTORIAL_SEGMENT,
            LADDER_TUTORIAL_TITLE,
            is_ladder_tutorial_task_group,
        )
        from games.models import LadderOffer

        if is_ladder_tutorial_task_group(task_group):
            return LADDER_TUTORIAL_SEGMENT, LADDER_TUTORIAL_TITLE
        offer = LadderOffer.objects.filter(task_group_id=task_group.pk).first()
        if offer is not None:
            return offer.share_hash, (offer.author or 'Лесенка').strip() or 'Лесенка'
    elif game.id == 'salad':
        from games.models import WordSaladOffer

        offer = WordSaladOffer.objects.filter(task_group_id=task_group.pk).first()
        if offer is not None:
            return offer.share_hash, (offer.theme or 'Салатик').strip() or 'Салатик'
    elif game.id == 'alphabetty':
        from games.models import AlphabettyOffer

        offer = AlphabettyOffer.objects.filter(task_group_id=task_group.pk).first()
        if offer is not None:
            return offer.share_hash, f'Алфавитка #{offer.share_hash}'

    return 0, ''


def render_new_ui_task_card_html(
    request,
    task,
    team,
    current_mode,
    user=None,
    anon_key=None,
    game=None,
    replay_slot=None,
    placement=None,
    context_dicts=None,
):
    """
    HTML for one new-UI task card (#new-task-{id}). Used in JSON + WebSocket to avoid full page reload.
    Returns None when this page is not the new UI or when the task is rendered only inside proportions sheet.
    """
    context = new_ui_task_card_context(
        request, task, team, current_mode, user=user, anon_key=anon_key, game=game,
        replay_slot=replay_slot, placement=placement, context_dicts=context_dicts,
    )
    if context is None:
        return None
    return render(request, TASK_CARD_TEMPLATE, context).content.decode('UTF-8')


TASK_CARD_TEMPLATE = 'site/partials/task_card.html'


def new_ui_task_card_context(
    request,
    task,
    team,
    current_mode,
    user=None,
    anon_key=None,
    game=None,
    replay_slot=None,
    placement=None,
    context_dicts=None,
):
    """Template context of one new-UI task card, or None when it has no card."""
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return None
    task_group = task.task_group
    if task_group.view == 'proportions' and task.task_type == 'proportions':
        return None
    # A card update needs context for this task only.  Building the whole group
    # made every attempt reload every task's complete history and like totals.
    tasks = [task]
    slot = placement
    if slot is None:
        slot = GameTaskGroup.objects.filter(game=game, task_group=task_group).first()
    ctx_dicts = context_dicts
    if ctx_dicts is None:
        ctx_dicts = build_task_group_task_context_dicts(
            game, task_group, tasks, team, user, anon_key, current_mode,
            placement=slot, replay_slot=replay_slot,
        )
    tg_number, tg_name = _task_card_public_identity(game, task_group, slot)
    # Daily section cards deliberately hide the task number.  The full page
    # passes this flag through task_group.html; keep the same presentation
    # when a card is replaced after an attempt or a hint.
    from games.daily.page.section import uses_daily_play_layout
    is_daily_single_task = uses_daily_play_layout(game.id)
    statistics_url = daily_statistics_url(
        game.id,
        slot.number if slot is not None else None,
        enabled=is_daily_single_task and slot is not None,
    )
    difficulty = None
    if is_daily_single_task and slot is not None:
        from games.daily.difficulty.core import get_cached_game_difficulties
        difficulty = get_cached_game_difficulties([slot]).get(slot.pk)
    from games.results.share import share_host_from_request
    return {
        'game': game,
        'task_group': task_group,
        'tg_number': tg_number,
        'tg_name': tg_name,
        'share_host': share_host_from_request(request),
        'is_daily_single_task': is_daily_single_task,
        'daily_statistics_url': statistics_url,
        'difficulty': difficulty,
        'task': task,
        'mode': current_mode,
        'team': team,
        'request': request,
        'replay_slot': replay_slot,
        'has_profile_user': has_profile(request.user),
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
        **ctx_dicts,
    }


# applyNewUiTaskHtml replaces #new-task-<key>; task_group.html gives the heading this id.
TG_HEADING_FRAGMENT_KEY = 'tg-heading'


def update_task_html(request, task, team, current_mode, user=None, anon_key=None, game=None, replay_slot=None):
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return {}

    group_tasks = list(task.task_group.tasks.visible())
    update_extra_tasks = [extra_task for extra_task in group_tasks if extra_task.task_type == 'text_with_forms']
    for extra_task in group_tasks:
        if "should_be_hidden_if_not_solved" in extra_task.tags:
            update_extra_tasks.append(extra_task)

    tasks_to_patch = update_extra_tasks + [task]

    # Карточки нового UI (JSON + WebSocket: static/js/track_ws.js, applyNewUiTaskHtml).
    # text_with_forms и скрытые до решения задания группы зависят от ответа на
    # это задание — перерисовываем их вместе с ним.
    # Ключи — строки: Redis/msgpack (channels_redis) с strict_map_key не принимает int keys.
    placement = GameTaskGroup.placement_for(game, task.task_group)
    if placement is not None:
        placement.task_group = task.task_group
    context_dicts = build_task_group_task_context_dicts(
        game,
        task.task_group,
        tasks_to_patch,
        team,
        user,
        anon_key,
        current_mode,
        placement=placement,
        replay_slot=replay_slot,
    )
    new_fragments = {}
    for t in tasks_to_patch:
        frag = render_new_ui_task_card_html(
            request, t, team, current_mode, user=user, anon_key=anon_key, game=game,
            replay_slot=replay_slot, placement=placement, context_dicts=context_dicts,
        )
        if frag:
            new_fragments[str(t.id)] = frag
    # Task-group name with ______ blanks fills in as its tasks get solved.
    heading_html = context_dicts.get('tg_heading_html')
    if heading_html and new_fragments:
        new_fragments[TG_HEADING_FRAGMENT_KEY] = '<strong id="new-task-{}">{}</strong>'.format(
            TG_HEADING_FRAGMENT_KEY, heading_html,
        )
    if new_fragments:
        return {'update_task_html_new': new_fragments}
    return {}
