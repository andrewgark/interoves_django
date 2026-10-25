import json
import re
from django.template.defaulttags import register
from allauth.socialaccount.models import SocialApp
from django.utils import timezone
from games.access import game_is_going_now
from games.models import GameTaskGroup, Like, Attempt, Team, Task, Registration
from games.util import clean_text, better_status


@register.simple_tag(takes_context=True)
def auth_next(context):
    """Return a safe local post-login target, falling back to the current URL."""
    request = context.get('request')
    if request is None:
        return '/'
    candidate = (request.GET.get('next') or '').strip()
    if candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return request.get_full_path()


@register.filter
def same_results_actor(actor, current_actor):
    """Compare result actors by stable team/user/anonymous identity."""
    from games.results.table.context import results_actors_match

    return results_actors_match(actor, current_actor)


@register.filter
def public_author(value):
    """Hide legacy Telegram suffixes from author labels shown on game pages."""
    return re.sub(r'\s*\(@[A-Za-z0-9_]{1,64}\)\s*$', '', str(value or '')).strip()


@register.filter
def social_app_configured(provider_id):
    """True if django-allauth has a SocialApplication for this provider (avoids DoesNotExist in templates)."""
    return SocialApp.objects.filter(provider=provider_id).exists()


@register.simple_tag
def current_year():
    """Returns the current year as an integer."""
    return timezone.now().year


@register.filter(name='one_more')
def one_more(_1, _2):
    return _1, _2


@register.filter
def get_item(dictionary, key):
    if hasattr(dictionary, 'get'):
        return dictionary.get(key)
    try:
        return dictionary[key]
    except (TypeError, KeyError, IndexError):
        return None


@register.filter
def currency_symbol(currency):
    """Use familiar symbols in UI while keeping ISO codes in payment data."""
    return {
        'AMD': '֏',
        'EUR': '€',
    }.get(str(currency or '').upper(), currency or '')


@register.filter
def payment_status_label(status):
    """Russian labels for ticket/donation statuses shown in player UI."""
    return {
        'Pending': 'Ожидает',
        'Accepted': 'Оплачен',
        'Confirmed': 'Пришёл',
        'Rejected': 'Отклонён',
    }.get(str(status or ''), status or '')


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
def sub_clamp0(a, b):
    """
    a - b, clamped to 0. Works with Decimal/int/str (comma or dot).
    """
    if a is None:
        a = 0
    if b is None:
        b = 0
    try:
        # Decimal-safe when possible
        return max(0, a - b)
    except Exception:
        try:
            af = float(str(a).strip().replace(',', '.'))
        except Exception:
            af = 0.0
        try:
            bf = float(str(b).strip().replace(',', '.'))
        except Exception:
            bf = 0.0
        return max(0, af - bf)


def _ru_plural_form_int(n, one, few, many):
    n = abs(int(n))
    n_mod_100 = n % 100
    if 11 <= n_mod_100 <= 14:
        return many
    n_mod_10 = n % 10
    if n_mod_10 == 1:
        return one
    if 2 <= n_mod_10 <= 4:
        return few
    return many


@register.filter
def ru_points_word(points):
    """
    Русское склонение для "балл".
    - целые: 1 балл / 2-4 балла / 5+ баллов (с 11-14 исключением)
    - дробные: 1.5 балла, 0.5 балла, 2.1 балла
    """
    if points is None:
        return 'баллов'
    s = str(points).strip().replace(',', '.')
    # Для дробных чисел в русском обычно используется род. ед.: "балла"
    if '.' in s:
        try:
            # если после чистки это всё же целое (например "2.0") — склоняем как целое
            if float(s).is_integer():
                return _ru_plural_form_int(int(float(s)), 'балл', 'балла', 'баллов')
        except Exception:
            pass
        return 'балла'
    try:
        return _ru_plural_form_int(int(s), 'балл', 'балла', 'баллов')
    except Exception:
        return 'баллов'


@register.filter
def ru_punkt_word(n):
    """
    Русское склонение для "пункт" по целому числу: 1 пункт / 2-4 пункта / 5+ пунктов (11-14 исключение).
    """
    try:
        return _ru_plural_form_int(int(n), 'пункт', 'пункта', 'пунктов')
    except Exception:
        return 'пунктов'


@register.filter
def ru_attempt_word(n):
    """1 попытка / 2–4 попытки / 5+ попыток (11–14 — попыток)."""
    try:
        return _ru_plural_form_int(int(n), 'попытка', 'попытки', 'попыток')
    except Exception:
        return 'попыток'


@register.filter
def ru_day_word(n):
    """1 день / 2–4 дня / 5+ дней (11–14 — дней)."""
    try:
        return _ru_plural_form_int(int(n), 'день', 'дня', 'дней')
    except Exception:
        return 'дней'


@register.simple_tag
def meta_bar_hint_penalty(ai, task, alphabetty_hints=None):
    """Штраф подсказок для meta-bar: у алфавитки — буквенные (−1 каждая)."""
    if getattr(task, 'task_type', None) == 'alphabetty':
        from games.alphabetty.play import ALPHABETTY_HINT_PENALTY
        if alphabetty_hints not in (None, ''):
            try:
                return max(0, int(alphabetty_hints)) * ALPHABETTY_HINT_PENALTY
            except (TypeError, ValueError):
                pass
        return ai.get_sum_hint_penalty() if ai else 0
    return ai.get_sum_hint_penalty() if ai else 0


@register.filter
def ru_iz_punkt_word(n):
    """
    Русское склонение для "из N пунктов" (родительный падеж):
    - 1: "пункта"
    - 2-4: "пунктов"
    - 5+: "пунктов"
    (11-14 исключение тоже даёт "пунктов")
    """
    try:
        return _ru_plural_form_int(int(n), 'пункта', 'пунктов', 'пунктов')
    except Exception:
        return 'пунктов'


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
def access_needs_registration(game):
    """Турнир с регистрацией, игра ещё не закончилась (не зависит от команды)."""
    return game.has_access('needs_registration', team=None)


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
    from games.puzzles import get_puzzle
    puzzle = get_puzzle(attempt.task.task_type)
    shower = getattr(puzzle, 'show_status', None) if puzzle is not None else None
    if shower is not None:
        return shower(attempt)
    if attempt.task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint', 'proportions', 'grid-puzzle', 'html_forms'):
        return attempt.status
    raise Exception('Unknown task_type {}'.format(attempt.task.task_type))


def _chain_progress_signature(attempt):
    """Что уже решено после попытки. Подсказки и штраф баллов сюда не входят.

    None — у попытки нет читаемого состояния, и прогресс остаётся на совести баллов.
    """
    task_type = getattr(getattr(attempt, 'task', None), 'task_type', None)
    try:
        state = json.loads(getattr(attempt, 'state', None) or '')
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    from games.puzzles import get_puzzle
    puzzle = get_puzzle(task_type)
    signature = getattr(puzzle, 'progress_signature', None) if puzzle is not None else None
    if signature is not None:
        return signature(state)
    return None


def _signature_grew(previous, current):
    """Текущее решённое множество строго шире предыдущего, без потерь."""
    if not previous or not current or previous[0] != current[0]:
        return False
    if previous[0] == 'slots':
        old = dict(previous[1])
        new = dict(current[1])
        if set(old) - set(new):
            return False
        grew = False
        for line, indexes in new.items():
            before = set(old.get(line, ()))
            after = set(indexes)
            if not before <= after:
                return False
            grew = grew or before < after
        return grew
    for before, after in zip(previous[1:], current[1:]):
        before_set = set(before)
        after_set = set(after)
        if not before_set <= after_set:
            return False
        if before_set < after_set:
            return True
    return False


def _chain_progress_ids(items, order):
    """Попытки, которые расширили решённую часть, даже если балл не вырос."""
    from games.puzzles import get_puzzle

    progressed = set()
    seen = {}
    raddle_solved = {}
    for index in order:
        attempt = items[index]
        task = getattr(attempt, 'task', None)
        task_type = getattr(task, 'task_type', None)
        key = (task_type, getattr(task, 'pk', None))
        puzzle = get_puzzle(task_type)
        opener = getattr(puzzle, 'submission_opened', None) if puzzle is not None else None
        if opener is not None:
            opened, solved_now = opener(attempt, raddle_solved.get(key, set()))
            if opened:
                progressed.add(id(attempt))
            if solved_now is not None:
                raddle_solved[key] = solved_now
            continue
        signature = _chain_progress_signature(attempt)
        if signature is None:
            continue
        previous = seen.get(key)
        if previous is None:
            previous = (signature[0],) + tuple(() for _ in signature[1:])
        if _signature_grew(previous, signature):
            progressed.add(id(attempt))
        seen[key] = signature
    return progressed


@register.filter
def attempts_with_status(attempts):
    """
    Помечает попытки для списка: 'ok' / 'partial' / 'wrong' / 'pending'.
    Для цепочечных заданий статус попытки часто описывает всё задание, а не этот ход.
    Ход частично верный, если вырос балл или расширилось решённое множество.
    Подсказка сама по себе множество не расширяет и остаётся неверной.
    """
    from decimal import Decimal
    from games.models import CHAIN_TASK_TYPES

    items = list(attempts or [])
    gained_ids = set()
    order = sorted(range(len(items)), key=lambda i: getattr(items[i], 'time', None) or 0)
    best = Decimal('0')
    for i in order:
        a = items[i]
        if getattr(a.task, 'task_type', None) not in CHAIN_TASK_TYPES:
            continue
        try:
            pts = Decimal(str(a.points or 0))
        except (ArithmeticError, ValueError, TypeError):
            pts = Decimal('0')
        if pts > best:
            gained_ids.add(id(a))
            best = pts
    progress_ids = _chain_progress_ids(items, order)

    result = []
    for a in items:
        is_chain = getattr(a.task, 'task_type', None) in CHAIN_TASK_TYPES
        if a.status == 'Ok':
            mark = 'ok'
        elif a.status == 'Pending':
            # Pending is a review state, not evidence that the submitted
            # chain step was wrong.  Never replace it with a progress verdict.
            mark = 'pending'
        elif is_chain:
            mark = 'partial' if id(a) in gained_ids or id(a) in progress_ids else 'wrong'
        elif a.status == 'Partial':
            mark = 'partial'
        elif a.status == 'Wrong':
            mark = 'wrong'
        else:
            mark = 'pending'
        label = {
            'ok': 'верно',
            'partial': 'частично',
            'wrong': 'неверно',
            'pending': 'проверяется',
        }[mark]
        result.append({'attempt': a, 'mark': mark, 'label': label})
    return result


@register.filter
def get_diff_points(attempt):
    from games.puzzles import get_puzzle
    puzzle = get_puzzle(attempt.task.task_type)
    points = getattr(puzzle, 'diff_points', None) if puzzle is not None else None
    if points is not None:
        return points(attempt)
    if attempt.task.task_type in ('default', 'with_tag', 'distribute_to_teams', 'autohint', 'proportions', 'grid-puzzle', 'html_forms'):
        return 0
    raise Exception('Unknown task_type {}'.format(attempt.task.task_type))


@register.filter
def get_exptiles(wall_attempts_info, mode):
    wall, attempts_info = wall_attempts_info
    return wall.get_exptiles(attempts_info, mode)


@register.filter
def json_encode(obj):
    return json.dumps(obj)


@register.filter
def get_wall_tile_stop_guessing_class(wall_attempts_info, mode):
    wall, attempts_info = wall_attempts_info
    if wall.guessing_tiles_is_over(attempts_info, mode=mode):
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
        for other_task in task.task_group.tasks.visible():
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
        ', '.join([str(x.number) for x in sorted(res, key=lambda h: h.key_sort())])
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
    if callable(getattr(attempts_info, 'get_hint_numbers', None)):
        return ', '.join(str(number) for number in attempts_info.get_hint_numbers())
    hint_attempts = [ha for ha in attempts_info.hint_attempts if ha.is_real_request]
    return ', '.join([
        str(ha.hint.number)
        for ha in sorted(hint_attempts, key=lambda ha: ha.hint.key_sort())
    ])


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


def _game_image_url(game):
    return game.image.url if game.image else ''


@register.filter
def get_future_games_js_list(games):
    if not games:
        return '[]'
    now = timezone.now()
    games_list = [
        '{{"id": "{id}", "title": "{title}", "startTime": new Date("{start_time}"), "endTime": new Date("{end_time}"), "imgSrc": "{img_src}"}}'.format(
            id=game.id,
            title=game.name,
            img_src=_game_image_url(game),
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
        img_src=_game_image_url(game),
        start_time=game.start_time.isoformat(),
        end_time=game.end_time.isoformat()
    )

@register.filter
def sorted_ticket_requests(team):
    return sorted(team.ticket_requests.all(), key=lambda x: x.time)


@register.simple_tag
def distribute_text_to_team_tag(task, team, game=None):
    data = json.loads(task.text)
    g = game or GameTaskGroup.resolve_game_for_task(task)
    team_number = None
    # Missing template variables (e.g. anon user → no profile) resolve to '' — not None.
    reg_fn = getattr(team, 'get_team_reg_number', None)
    if g is not None and callable(reg_fn):
        team_number = reg_fn(g)
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
    by_id = {t.id: t for t in Task.objects.visible().filter(id__in=ref_task_ids)}
    ref_tasks = [by_id[i] for i in ref_task_ids if i in by_id]
    if not ref_tasks:
        return False
    statuses = [get_task_status(((ref_task, team), mode), task_to_attempts_info[ref_task.id]) for ref_task in ref_tasks]
    return statuses != ["Ok"] * len(ref_tasks)


@register.filter
def word_salad_theme(text):
    """Theme for the salad widget: strip a stored «Тема:» so the label is not doubled."""
    from games.word_salad import theme_from_text
    return theme_from_text(text)


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
