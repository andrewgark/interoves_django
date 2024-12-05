from django.shortcuts import render, get_object_or_404
from games.models import Attempt, TaskGroup, ImageManager, AudioManager


def get_task_to_attempts_info(game, team, mode='general'):
    task_to_attempts_info = {}
    for task_group in game.task_groups.all():
        for task in task_group.tasks.all():
            task_to_attempts_info[task.id] = Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
    return task_to_attempts_info


def get_text_with_forms_to_html(request, text, normal_tasks, team, mode, skip_zero=True):
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
                    'attempts_info': Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
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
                    task.id: Attempt.manager.get_attempts_info(team=team, task=task, mode=mode)
                    for task in left_answers_tasks
                }
            }
        ).content.decode('UTF-8')
    )
    return html


def get_task_text_with_forms_to_html(request, task, team, mode):
    assert "text_with_forms" == task.task_type
    normal_tasks = sorted(
        task.task_group.tasks.all(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(request, task.text, normal_tasks, team, mode)


def get_task_group_title_text_with_forms_to_html(request, task_group, team, mode):
    assert "text_with_forms_in_name" in task_group.tags
    normal_tasks = sorted(
        task_group.tasks.all(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(request, task_group.name, normal_tasks, team, mode, skip_zero=False)


def get_game_title_text_with_forms_to_html(request, game, team, mode):
    assert "text_with_forms_in_name" in game.tags
    assert "text_with_forms_task_group_number" in game.tags
    task_group = get_object_or_404(
        TaskGroup,
        number=game.tags["text_with_forms_task_group_number"],
        game=game
    )
    normal_tasks = sorted(
        task_group.tasks.all(),
        key=lambda t: t.key_sort()
    )
    return get_text_with_forms_to_html(request, game.name, normal_tasks, team, mode, skip_zero=False)


def get_all_text_with_forms_to_html(request, game, team, mode):
    tasks = []
    for task_group in game.task_groups.all():
        tasks.extend(list(task_group.tasks.filter(task_type='text_with_forms')))
    result = {"tasks": {}, "task_groups": {}}
    for task in tasks:
        result["tasks"][task.id] = get_task_text_with_forms_to_html(request, task, team, mode)
    for task_group in game.task_groups.all():
        if "text_with_forms_in_name" in task_group.tags:
            result["task_groups"][task_group.number] = get_task_group_title_text_with_forms_to_html(
                    request, task_group, team, mode
            )
    if "text_with_forms_in_name" in game.tags:
        result["game"] = get_game_title_text_with_forms_to_html(request, game, team, mode)
    return result


def render_task(task, request, team, current_mode):
    task_text_with_forms_to_html = {}
    if task.task_type == 'text_with_forms':
        task_text_with_forms_to_html = {task.id: get_task_text_with_forms_to_html(request, task, team, current_mode)}
    return render(request, 'task.html', {
        'task': task,
        'task_group': task.task_group,
        'task_to_attempts_info': get_task_to_attempts_info(task.task_group.game, team, current_mode),
        'attempts_info': Attempt.manager.get_attempts_info(team=team, task=task, mode=current_mode),
        'mode': current_mode,
        'team': team,
        'task_text_with_forms_to_html': task_text_with_forms_to_html,
        'image_manager': ImageManager(),
        'audio_manager': AudioManager(),
    }).content.decode('UTF-8')


def render_task_group_title(task_group, request, team, current_mode):
    task_group_text_with_forms_to_html = {}
    if "text_with_forms_in_name" in task_group.tags:
        task_group_text_with_forms_to_html = {task_group.number: get_task_group_title_text_with_forms_to_html(request, task_group, team, current_mode)}
    return render(request, 'task-group-title.html', {
        'task_group': task_group,
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


def update_task_html(request, task, team, current_mode):
    game = task.task_group.game
    
    update_extra_tasks = list(task.task_group.tasks.filter(task_type='text_with_forms'))
    for extra_task in task.task_group.tasks.all():
        if "should_be_hidden_if_not_solved" in extra_task.tags:
            update_extra_tasks.append(extra_task)

    update_html = {
        'update_task_html': {
            t.id: render_task(t, request, team, current_mode)
            for t in update_extra_tasks + [task]
        },
        'update_task_group_title_html': {
            task.task_group.number: render_task_group_title(task.task_group, request, team, current_mode)
        },
        'update_game_title_html': render_game_title(game, request, team, current_mode),
    }
    return update_html
