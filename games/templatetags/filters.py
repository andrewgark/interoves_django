import json
from django.template.defaulttags import register
from games.access import game_is_going_now
from games.models import Like
from games.util import clean_text


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
