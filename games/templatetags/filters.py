from django.template.defaulttags import register
from games.access import game_is_going_now


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
