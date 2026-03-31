from django.shortcuts import render, get_object_or_404

from games.views.new_ui import (
    NEW_UI_PROJECT,
    NEW_UI_SECTIONS_PROJECT,
    build_task_group_task_context_dicts,
)
from games.views.util import has_profile
from games.models import Attempt, GameTaskGroup, ImageManager, AudioManager


def get_task_to_attempts_info(game, team, mode='general'):
    task_to_attempts_info = {}
    for link in game.task_group_links.select_related('task_group').order_by('number'):
        tg = link.task_group
        for task in tg.tasks.visible():
            task_to_attempts_info[task.id] = Attempt.manager.get_attempts_info(
                team=team, task=task, mode=mode, game=game,
            )
    return task_to_attempts_info


def get_text_with_forms_to_html(request, text, normal_tasks, team, mode, skip_zero=True, game=None):
    htmls = []
    split_text = text.split('______')
    for i, text_part in enumerate(split_text):
        if skip_zero:
            n = i + 1
        else:
            n = i
        htmls.append(text_part)
        if len(normal_tasks) <= n or i == len(split_text) - 1:
            continue
        task = normal_tasks[n]
        htmls.append(
            render(
                request,
                'task-content/attempt-simple-form.html',
                {
                    'task': task,
                    'game': game,
                    'attempts_info': Attempt.manager.get_attempts_info(
                        team=team, task=task, mode=mode, game=game,
                    ),
                }
            ).content.decode('UTF-8')
        )
    html = ''.join(htmls)

    left_answers_tasks = normal_tasks[1:]
    left_answers_tasks.sort(key=lambda t: t.answer)
    html = html.replace('LEFT_ANSWERS', render(
            request,
            'task-content/left-answers.html',
            {
                'left_answers_tasks': left_answers_tasks,
                'task_to_attempts_info': {
                    t.id: Attempt.manager.get_attempts_info(team=team, task=t, mode=mode, game=game)
                    for t in left_answers_tasks
                }
            }
        ).content.decode('UTF-8')
    )
    return html


def get_task_text_with_forms_to_html(request, task, team, mode, game=None):
    assert "text_with_forms" == task.task_type
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    normal_tasks = sorted(
        task.task_group.tasks.visible(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(request, task.text, normal_tasks, team, mode, game=game)


def get_task_group_title_text_with_forms_to_html(request, game, task_group, team, mode):
    assert "text_with_forms_in_name" in task_group.tags
    link = get_object_or_404(GameTaskGroup, game=game, task_group=task_group)
    normal_tasks = sorted(
        task_group.tasks.visible(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(
        request, link.name, normal_tasks, team, mode, skip_zero=False, game=game,
    )


def get_game_title_text_with_forms_to_html(request, game, team, mode):
    assert "text_with_forms_in_name" in game.tags
    assert "text_with_forms_task_group_number" in game.tags
    link = get_object_or_404(
        GameTaskGroup,
        game=game,
        number=game.tags["text_with_forms_task_group_number"],
    )
    task_group = link.task_group
    normal_tasks = sorted(
        task_group.tasks.visible(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(request, game.name, normal_tasks, team, mode, skip_zero=False, game=game)


def get_all_text_with_forms_to_html(request, game, team, mode):
    tasks = []
    for link in game.task_group_links.select_related('task_group'):
        tasks.extend(list(link.task_group.tasks.visible().filter(task_type='text_with_forms')))
    result = {"tasks": {}, "task_groups": {}}
    for task in tasks:
        result["tasks"][task.id] = get_task_text_with_forms_to_html(
            request, task, team, mode, game=game,
        )
    for link in game.task_group_links.select_related('task_group'):
        tg = link.task_group
        if "text_with_forms_in_name" in tg.tags:
            result["task_groups"][link.number] = get_task_group_title_text_with_forms_to_html(
                request, game, tg, mode,
            )
    if "text_with_forms_in_name" in game.tags:
        result["game"] = get_game_title_text_with_forms_to_html(request, game, team, mode)
    return result


def render_task(task, request, team, current_mode, game=None):
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    task_text_with_forms_to_html = {}
    if task.task_type == 'text_with_forms':
        task_text_with_forms_to_html = {
            task.id: get_task_text_with_forms_to_html(request, task, team, current_mode, game=game),
        }
    slot = None
    if game is not None:
        slot = GameTaskGroup.objects.filter(game=game, task_group=task.task_group).first()
    return render(request, 'task.html', {
        'task': task,
        'task_group': task.task_group,
        'game': game,
        'tg_number': slot.number if slot else 0,
        'task_to_attempts_info': get_task_to_attempts_info(game, team, current_mode) if game else {},
        'attempts_info': Attempt.manager.get_attempts_info(
            team=team, task=task, mode=current_mode, game=game,
        ) if game else Attempt.manager.get_attempts_info(team=team, task=task, mode=current_mode),
        'mode': current_mode,
        'team': team,
        'task_text_with_forms_to_html': task_text_with_forms_to_html,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
    }).content.decode('UTF-8')


def render_task_group_title(task_group, request, team, current_mode, game):
    link = get_object_or_404(GameTaskGroup, game=game, task_group=task_group)
    task_group_text_with_forms_to_html = {}
    if "text_with_forms_in_name" in task_group.tags:
        task_group_text_with_forms_to_html = {
            link.number: get_task_group_title_text_with_forms_to_html(
                request, game, task_group, team, current_mode,
            ),
        }
    return render(request, 'task-group-title.html', {
        'task_group': task_group,
        'game': game,
        'tg_number': link.number,
        'tg_name': link.name,
        'mode': current_mode,
        'team': team,
        'task_group_text_with_forms_to_html': task_group_text_with_forms_to_html,
    }).content.decode('UTF-8')


def render_game_title(game, request, team, current_mode):
    game_text_with_forms_to_html = None
    if "text_with_forms_in_name" in game.tags:
        game_text_with_forms_to_html = get_game_title_text_with_forms_to_html(request, game, team, current_mode)
    return render(request, 'game-title.html', {
        'game': game,
        'mode': current_mode,
        'team': team,
        'game_text_with_forms_to_html': game_text_with_forms_to_html,
    }).content.decode('UTF-8')


def render_new_ui_task_card_html(request, task, team, current_mode, user=None, anon_key=None, game=None):
    """
    HTML for one new-UI task card (#new-task-{id}). Used in JSON + WebSocket to avoid full page reload.
    Returns None when this page is not the new UI or when the task is rendered only inside proportions sheet.
    """
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None or game.project_id not in (NEW_UI_PROJECT, NEW_UI_SECTIONS_PROJECT):
        return None
    task_group = task.task_group
    if task_group.view == 'proportions' and task.task_type == 'proportions':
        return None
    tasks = sorted(task_group.tasks.visible(), key=lambda t: t.key_sort())
    ctx_dicts = build_task_group_task_context_dicts(
        game, task_group, tasks, team, user, anon_key, current_mode,
    )
    slot = GameTaskGroup.objects.filter(game=game, task_group=task_group).first()
    tg_number = slot.number if slot else 0
    tg_name = slot.name if slot else ''
    return render(request, 'new/partials/task_card.html', {
        'game': game,
        'task_group': task_group,
        'tg_number': tg_number,
        'tg_name': tg_name,
        'task': task,
        'mode': current_mode,
        'team': team,
        'request': request,
        'has_profile_user': has_profile(request.user),
        **ctx_dicts,
    }).content.decode('UTF-8')


def update_task_html(request, task, team, current_mode, user=None, anon_key=None, game=None):
    if game is None:
        game = GameTaskGroup.resolve_game_for_task(task)
    if game is None:
        return {}

    update_extra_tasks = list(task.task_group.tasks.visible().filter(task_type='text_with_forms'))
    for extra_task in task.task_group.tasks.visible():
        if "should_be_hidden_if_not_solved" in extra_task.tags:
            update_extra_tasks.append(extra_task)

    tasks_to_patch = update_extra_tasks + [task]
    link = GameTaskGroup.objects.filter(game=game, task_group=task.task_group).first()
    slot_number = link.number if link else 0
    update_html = {
        'update_task_html': {
            t.id: render_task(t, request, team, current_mode, game=game)
            for t in tasks_to_patch
        },
        'update_task_group_title_html': {
            slot_number: render_task_group_title(task.task_group, request, team, current_mode, game),
        },
        'update_game_title_html': render_game_title(game, request, team, current_mode),
    }
    new_fragments = {}
    for t in tasks_to_patch:
        frag = render_new_ui_task_card_html(
            request, t, team, current_mode, user=user, anon_key=anon_key, game=game,
        )
        if frag:
            new_fragments[t.id] = frag
    if new_fragments:
        update_html['update_task_html_new'] = new_fragments
    return update_html
