import json
from django.template.defaulttags import register
from games.access import game_is_going_now
from games.models import Like, Attempt
from games.util import clean_text, better_status


@register.filter(name='one_more')
def one_more(_1, _2):
    return _1, _2


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def order_by(queryset, args):
    args = [x.strip() for x in args.split(',')]
    return queryset.order_by(*args)


@register.filter
def minimize_digits(points):
    str_points = str(points)
    cleaned_points = str_points
    if '.' in str_points or ',' in str_points:
        cleaned_points = str_points.strip('0').strip('.,')
    if not cleaned_points:
        cleaned_points = '0'
    return cleaned_points


@register.filter
def get_new_attempt_number(n):
    if not n:
        return 1
    return int(n) + 1


@register.filter
def get_parity(n):
    if n % 2 == 0:
        return 'even'
    return 'odd'


@register.filter
def get_lower_status(status):
    if not status:
        return 'no'
    return status.lower()


@register.filter
def get_ru_status(status):
    status_to_ru = {
        'Wrong': 'неверно',
        'Partial': 'частичное решение',
        'Pending': 'проверяется ведущим',
        'Ok': 'верно',
    }
    return status_to_ru.get(status, '')


@register.filter
def access_play_without_team(game, team):
    return game.has_access('play_without_team', team=team)


@register.filter
def access_play_with_team(game, team):
    return game.has_access('play_with_team', team=team)


@register.filter
def access_see_results(game, team):
    return game.has_access('see_results', team=team)


@register.filter
def access_see_tournament_results(game, team):
    return game.has_access('see_tournament_results', team=team)


@register.filter
def access_see_answer(game, team):
    return game.has_access('see_answer', team=team)


@register.filter
def access_read_googledoc(game, team):
    return game.has_access('read_googledoc', team=team)


@register.filter
def is_going_now(game):
    return game_is_going_now(game)


@register.filter
def get_not_guessed_tiles(wall, attempts_info):
    return wall.get_not_guessed_tiles(attempts_info)


@register.filter
def get_guessed_tiles(wall, attempts_info):
    return wall.get_guessed_tiles(attempts_info)


@register.filter
def get_show_status(attempt):
    if attempt.task.task_type == 'default':
        return attempt.status
    elif attempt.task.task_type == 'wall':
        if attempt.status == 'Pending':
            return 'Pending'
        return json.loads(attempt.state)['last_attempt']['status']
    raise Exception('Unknown task_type {}'.format(attempt.task.task_type))


@register.filter
def get_diff_points(attempt):
    if attempt.task.task_type == 'default':
        return 0
    elif attempt.task.task_type == 'wall':
        return json.loads(attempt.state)['last_attempt']['points']
    raise Exception('Unknown task_type {}'.format(attempt.task.task_type))


@register.filter
def get_exptiles(wall_attempts_info, mode):
    wall, attempts_info = wall_attempts_info
    return wall.get_exptiles(attempts_info, mode)


@register.filter
def json_encode(obj):
    return json.dumps(obj)


@register.filter
def get_wall_tile_stop_guessing_class(wall, attempts_info):
    if wall.guessing_tiles_is_over(attempts_info):
        return 'wall-tile-stop-guessing'
    return ''


@register.filter
def get_likes(task):
    return Like.manager.get_likes(task)


@register.filter
def get_dislikes(task):
    return Like.manager.get_dislikes(task)


@register.filter
def team_has_like(task, team):
    return Like.manager.team_has_like(task, team)


@register.filter
def team_has_dislike(task, team):
    return Like.manager.team_has_dislike(task, team)


@register.filter
def get_task_status(task_team__mode, attempts_info):
    task_team, mode = task_team__mode
    task, team = task_team
    if attempts_info is not None and attempts_info.best_attempt is not None:
        return attempts_info.best_attempt.status
    if task.task_type == 'text_with_forms':
        worst_status = 'Ok'
        for other_task in task.task_group.tasks.all():
            attempts_info = Attempt.manager.get_attempts_info(team, other_task, mode)
            status = ''
            if attempts_info and attempts_info.best_attempt:
                status = attempts_info.best_attempt.status
            if other_task != task and better_status(worst_status, status):
                worst_status = status
        return worst_status
    return ''


@register.filter
def get_text_with_forms_attempt_form(task):
    return task.get_attempt_form(placeholder='Ответ ({})'.format(task.number))


@register.filter
def hint_was_taken(attempts_info, hint):
    for hint_attempt in attempts_info.hint_attempts:
        if hint_attempt.hint == hint:
            return True
    return False


@register.filter
def hint_was_really_taken(attempts_info, hint):
    for hint_attempt in attempts_info.hint_attempts:
        if hint_attempt.hint == hint and hint_attempt.is_real_request:
            return True
    return False


@register.filter
def get_hint_numbers(hint_attempts):
    return ''.join([str(han) for han in sorted([ha.number for ha in hint_attempts])])
