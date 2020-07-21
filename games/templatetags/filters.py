from django.template.defaulttags import register


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def order_by(queryset, args):
    args = [x.strip() for x in args.split(',')]
    return queryset.order_by(*args)


@register.filter
def minimize_digits(points):
    cleaned_points = str(points).strip('0,.')
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
        return 'wrong'
    return status.lower()


@register.filter
def game_is_available(game, team):
    return game.is_available(team)


@register.filter
def results_are_available(game, team):
    return game.results_are_available(team)


@register.filter
def tournament_results_are_available(game, team):
    return game.tournament_results_are_available(team)
