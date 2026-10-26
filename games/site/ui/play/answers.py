"""Main UI: hub, games folder, profile, team."""
import logging

from django.http import Http404
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods


from django.utils import timezone



from games.puzzles.replacements.grid_puzzle import GridPuzzleDataError, public_grid_puzzle_context
from games.models import (
    Attempt,
)
from games.puzzles.replacements.lines import (
    task_replacements_canonical_answer_row,
)
from games.puzzles.raddle.engine import (
    parse_raddle_data,
    raddle_word_solved_list,
)
from games.site.views.game.context import game_from_request_for_task
from games.site.views.pages.util import (
    effective_play_mode,
    get_public_task_or_404,
    has_profile,
)


from games.site.ui import (
    _anon_key_from_request,
    _get_play_mode,
)
from games.site.ui.play.replacements import _replacements_lines_line_done_list

logger = logging.getLogger('games.views.new_ui')

def _answer_popup_html(answer_text, answer_comment=None):
    """HTML for the new-task answer modal: bold answer plus optional comment (HTML allowed in comment, like legacy answer.html)."""
    c = (answer_comment or '').strip()
    if c:
        return format_html(
            '<div style="font-weight:700">{}</div>'
            '<div class="new-login-hint new-answer-comment" style="margin-top:0.75rem">{}</div>',
            answer_text or '',
            mark_safe(c),
        )
    return format_html('<div style="font-weight:700">{}</div>', answer_text or '')


@require_http_methods(['GET'])
def new_get_answer(request, task_id):
    task = get_public_task_or_404(task_id)
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()
    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
        else:
            anon_key = _anon_key_from_request(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    from games.club.access.archive import reject_if_club_archive_blocked

    locked = reject_if_club_archive_blocked(request, game, task=task, json_mode=True)
    if locked is not None:
        return locked

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(
        team=team, user=user, anon_key=anon_key, task=task, mode=mode, game=game,
    )
    if mode != 'general' and not attempts_info.is_solved():
        return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    if task.task_type == 'replacements_lines':
        return JsonResponse({
            'html': (
                '<div class="new-login-hint">Для замен ответ показывается отдельно по каждой строке '
                '(кнопка «Ответ» у строки после её решения).</div>'
            ),
        })

    if task.task_type == 'raddle':
        return JsonResponse({
            'html': (
                '<div class="new-login-hint">Для raddle ответ показывается у каждого '
                'решённого слова (кнопка «Ответ»).</div>'
            ),
        })

    if task.task_type == 'grid-puzzle':
        try:
            gp = public_grid_puzzle_context(task, reveal_solution=True, readonly=True)
        except GridPuzzleDataError:
            return JsonResponse({'html': '<div class="new-login-hint">Не удалось показать ответ.</div>'})
        return JsonResponse({
            'html': render_to_string(
                'new/task-content/grid-puzzle-answer.html',
                {'task': task, 'gp': gp},
                request=request,
            ),
        })

    return JsonResponse({'html': _answer_popup_html(task.answer, task.answer_comment)})


@require_http_methods(['GET'])
def new_get_replacements_line_answer(request, task_id, line_index):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'replacements_lines':
        raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()
    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game, task=task, json_mode=True)
    if locked is not None:
        return locked

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
        else:
            anon_key = _anon_key_from_request(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(
        team=team, user=user, anon_key=anon_key, task=task, mode=mode, game=game,
    )
    try:
        line_index_int = int(line_index)
    except (TypeError, ValueError):
        line_index_int = -1
    line_done_list = _replacements_lines_line_done_list(
        task, attempts_info, game=game, team=team, user=user, anon_key=anon_key,
        mode=mode,
    )
    if mode != 'general':
        task_complete = bool(line_done_list) and all(line_done_list)
        if not task_complete:
            if line_index_int < 0 or line_index_int >= len(line_done_list) or not line_done_list[line_index_int]:
                return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    # Для replacements_lines ответы живут в checker_data (output-текст).
    row = task_replacements_canonical_answer_row(task, line_index_int)
    text = ' | '.join(str(cell) for cell in (row or []))
    if not text.strip():
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    return JsonResponse({'html': _answer_popup_html(text, task.answer_comment)})


@require_http_methods(['GET'])
def new_get_raddle_word_answer(request, task_id, word_index):
    task = get_public_task_or_404(task_id)
    if task.task_type != 'raddle':
        raise Http404()
    game = game_from_request_for_task(request, task)
    if game is None:
        raise Http404()
    from games.site.views.game.context import unpublished_scheduled_task_response
    if unpublished_scheduled_task_response(request, game, task) is not None:
        raise Http404()
    from games.club.access.archive import reject_if_club_archive_blocked
    locked = reject_if_club_archive_blocked(request, game, task=task, json_mode=True)
    if locked is not None:
        return locked

    play_mode, _ = _get_play_mode(request, game.project_id)
    play_mode = effective_play_mode(play_mode, game, user=request.user)
    team = None
    user = None
    anon_key = None
    if play_mode == 'team':
        if not has_profile(request.user) or not request.user.profile.team_on:
            raise Http404()
        team = request.user.profile.team_on
        if not game.has_access('play', team=team):
            raise Http404()
    else:
        if request.user.is_authenticated:
            if not has_profile(request.user):
                raise Http404()
            user = request.user
        else:
            anon_key = _anon_key_from_request(request)
            if not anon_key:
                raise Http404()
        if not game.has_access('read_googledoc', team=None, attempt=Attempt(time=timezone.now())):
            raise Http404()

    mode = game.get_current_mode(Attempt(time=timezone.now()))
    attempts_info = Attempt.manager.get_attempts_info(team=team, user=user, anon_key=anon_key, task=task, mode=mode)
    try:
        word_index_int = int(word_index)
    except (TypeError, ValueError):
        word_index_int = -1
    word_done_list = []
    parsed = parse_raddle_data(task)
    if parsed:
        word_done_list = raddle_word_solved_list(
            parsed, attempts_info.attempts if attempts_info else [],
        )
    if mode != 'general':
        if not attempts_info.is_solved():
            if (
                word_index_int < 0
                or word_index_int >= len(word_done_list)
                or not word_done_list[word_index_int]
            ):
                return JsonResponse({'html': '<div class="new-login-hint">Ответ доступен после верного решения.</div>'})

    parsed = parse_raddle_data(task)
    if not parsed or word_index_int < 0 or word_index_int >= parsed['n_words']:
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    text = parsed['words'][word_index_int]
    if not text.strip():
        return JsonResponse({'html': '<div class="new-login-hint">Нет ответа.</div>'})
    return JsonResponse({'html': _answer_popup_html(text, task.answer_comment)})



__all__ = [
    '_answer_popup_html',
    'new_get_answer',
    'new_get_replacements_line_answer',
    'new_get_raddle_word_answer',
]
