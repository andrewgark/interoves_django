import json
from django.shortcuts import get_object_or_404
from django.template.defaulttags import register
from django.utils import timezone
from games.access import game_is_going_now
from games.models import Like, Attempt, Team, Task, Registration
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
        cleaned_points = str_points.rstrip('0').rstrip('.,')
        try:
            if '.' in cleaned_points:
                a, b = cleaned_points.split('.')
                cleaned_points = '{}.{}'.format(a, b[:3])
        except Exception:
            pass
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
def access_play(game, team):
    return game.has_access('play', team=team)


@register.filter
def access_is_registered(game, team):
    return game.has_access('is_registered', team=team)


@register.filter
def access_register(game, team):
    return game.has_access('register', team=team)



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
    if attempt.task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint'):
        return attempt.status
    elif attempt.task.task_type == 'wall':
        if attempt.status == 'Pending':
            return 'Pending'
        return json.loads(attempt.state)['last_attempt']['status']
    raise Exception('Unknown task_type {}'.format(attempt.task.task_type))


@register.filter
def get_diff_points(attempt):
    if attempt.task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint'):
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
    placeholder = 'Ответ ({})'.format(task.number)
    width = None
    if task.field_text_width is not None and task.field_text_width < 15:
        placeholder = ''
    return task.get_attempt_form(placeholder=placeholder)


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
def all_not_taken_required_hints(attempts_info, hint):
    already_taken = set([hint_attempt.hint for hint_attempt in attempts_info.hint_attempts])
    res = []
    for required_hint in hint.required_hints.all():
        if required_hint not in already_taken:
            res.append(required_hint)
    if len(res) == 0:
        return False
    if len(res) == 1:
        return 'подсказки {}'.format(res[0].number)
    return 'подсказок {}'.format(
        ', '.join([str(x) for x in sorted([h.number for h in res])])
    )

@register.filter
def took_at_least_one_hint(attempts_info):
    if not attempts_info:
        return False
    for hint_attempt in attempts_info.hint_attempts:
        if hint_attempt.is_real_request:
            return True
    return False


@register.filter
def get_hint_numbers(attempts_info):
    hint_attempts = [ha for ha in attempts_info.hint_attempts if ha.is_real_request]
    return ', '.join([str(han) for han in sorted([ha.hint.number for ha in hint_attempts])])


@register.filter
def make_hint_desc_in_brackets(hint_desc):
    if not hint_desc:
        return ''
    return '({}) '.format(hint_desc)


@register.filter
def wall_tile_has_image(tile_text):
    return tile_text.startswith('IMAGE_ID=') or tile_text.startswith('image_id=')


@register.filter
def get_wall_tile_image(tile_text, image_manager):
    image_id = tile_text[len('IMAGE_ID='):]
    return image_manager.get_image(image_id)


@register.filter
def wall_tile_has_audio(tile_text):
    return tile_text.startswith('AUDIO_ID=') or tile_text.startswith('audio_id=')


@register.filter
def get_wall_tile_audio(tile_text, audio_manager):
    audio_id = tile_text[len('AUDIO_ID='):]
    return audio_manager.get_audio(audio_id)


@register.filter
def wall_tile_has_link(tile_text):
    parts = sorted(tile_text.split('&'))
    if len(parts) != 2:
        return False
    return (parts[0].startswith('ID=') or parts[0].startswith('id=')) or \
           (parts[1].startswith('LINK=') or parts[1].startswith('link=')) 


@register.filter
def get_wall_tile_link_html(tile_text):
    parts = sorted(tile_text.split('&'))
    link = parts[1][len('LINK='):]
    element_id = parts[0][len('ID='):]
    return f'<a href="{link}" style="text-decoration: underline blue;color: blue !important;font-size: 30px;line-height:80px;">{element_id}</a>'


@register.filter
def get_future_games_js_list(games):
    if not games:
        return '[]'
    now = timezone.now()
    games_list = [
        '{{"id": "{id}", "title": "{title}", "startTime": new Date("{start_time}"), "endTime": new Date("{end_time}"), "imgSrc": "{img_src}"}}'.format(
            id=game.id,
            title=game.name,
            img_src=game.image.url,
            start_time=game.start_time.isoformat(),
            end_time=game.end_time.isoformat()
        )
        for game in games
        if game.start_time > now
    ]
    games_joined = ', '.join(games_list)
    return '[{}]'.format(games_joined)


@register.filter
def get_ongoing_game_js(game):
    if not game:
        return 'null'
    return '{{"id": "{id}", "title": "{title}", "startTime": new Date("{start_time}"), "endTime": new Date("{end_time}"), "imgSrc": "{img_src}"}}'.format(
        id=game.id,
        title=game.name,
        img_src=game.image.url,
        start_time=game.start_time.isoformat(),
        end_time=game.end_time.isoformat()
    )

@register.filter
def sorted_ticket_requests(team):
    return sorted(team.ticket_requests.all(), key=lambda x: x.time)


@register.filter
def distribute_text_to_team(task, team):
    data = json.loads(task.text)
    team_number = None
    if team is not None:
        team_number = team.get_team_reg_number(task.task_group.game)
    if team_number is None:
        distr_text = 'Увидеть уникальную раздатку могут только зарегистрированные во время игры команды.'
    elif team_number > len(data['list']):
        distr_text = 'Похоже, зарегистрировалось слишком много команд, и вам ничего не досталось. Напишите про это в чат участников, постараемся исправить.'
    else:
        distr_text = data['list'][team_number]

    return '{}<br>\n{}'.format(
        data['text'],
        distr_text
    )


@register.filter
def team_tagged(task):
    team_name = task.tags.get('team')
    if team_name is None:
        return None
    return Team.objects.get(name=team_name)


@register.filter
def task_should_be_hidden(task_team__mode, task_to_attempts_info):
    task_team, mode = task_team__mode
    task, team = task_team
    if "should_be_hidden_if_not_solved" not in task.tags:
        return False
    if team is None:
        return True
    ref_task_ids = task.tags["should_be_hidden_if_not_solved"]
    ref_tasks = [get_object_or_404(Task, id=x) for x in ref_task_ids]
    statuses = [get_task_status(((ref_task, team), mode), task_to_attempts_info[ref_task.id]) for ref_task in ref_tasks]
    return statuses != ["Ok"] * len(ref_tasks)


@register.filter
def replace_edit_copy(url):
    return url.replace("edit", "copy")

@register.filter
def get_available_referents(game, team):
    referents = []
    game_registrations = Registration.objects.filter(game=game)
    for r in game_registrations:
        if r.team.referer == team:
            referent = r.team
            referent_registrations = Registration.objects.filter(team=team, with_referent=referent)
            if len(referent_registrations) < 3:
                referents.append(referent)
    return referents
