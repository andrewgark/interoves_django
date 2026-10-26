"""Main UI: hub, games folder, profile, team."""
import json
import logging





from games.site.session.gameplay_context import issue_gameplay_context

from games.daily.page.board import (
    get_daily_board_adapter,
)
from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
)
from games.puzzles.replacements.grid_puzzle import GridPuzzleDataError, public_grid_puzzle_context
from games.tasks.titles import raddle_share_title, task_display_name
from games.tasks.result_details import (
    chain_state_for_actor,
)
from games.tasks.presentation import task_ui_descriptor, wall_ui_context
from games.tasks.replacements_state import current_state as replacements_current_state
from games.tasks.actor_state import chain_state_for_actor
from games.models import (
    Attempt,
    GameTaskGroup,
    Like,
)
from games.puzzles.replacements.lines import (
    parse_replacements_lines_text,
    replacements_solved_slots_from_state,
)
from games.puzzles.salad.rules import (
    WORD_SALAD_GAME_ID,
)
from games.puzzles.replacements.proportions import build_proportions_chips_for_tasks
from games.puzzles.replacements.forms import render_html_forms_task


from games.site.ui.results.pages import _raddle_ui_state_for_actor

logger = logging.getLogger('games.views.new_ui')

def _task_ui_descriptor(
    task, *, rld=None, html_forms=None, rd=None, wall_meta=None, ws=None, gp=None,
    board_context_key=None, body_template_override=None,
    body_wrapper_override=None, show_attempts_override=None,
    show_answer_override=None,
):
    """Compatibility wrapper for the task-card presentation contract."""
    return task_ui_descriptor(
        task,
        rld=rld,
        html_forms=html_forms,
        rd=rd,
        wall_meta=wall_meta,
        ws=ws,
        gp=gp,
        board_context_key=board_context_key,
        body_template_override=body_template_override,
        body_wrapper_override=body_wrapper_override,
        show_attempts_override=show_attempts_override,
        show_answer_override=show_answer_override,
    )


def _wall_ui_context(task, attempts_info, mode):
    """Compatibility wrapper for wall task presentation."""
    return wall_ui_context(task, attempts_info, mode)


def _replacements_current_state(
    task, *, game=None, team=None, user=None, anon_key=None, mode='general',
    replay_slot=None, attempts_info=None,
):
    """Compatibility wrapper for replacements state resolution."""
    return replacements_current_state(
        task,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts_info=attempts_info,
    )


def build_task_group_task_context_dicts(game, task_group, tasks, team, user, anon_key, mode, placement=None, replay_slot=None, request=None):
    """
    Shared context for task_group.html and new/partials/task_card.html
    (attempts, walls, replacements_lines, likes, proportions pool).
    """
    attempts_info_by_task_id = Attempt.manager.get_bulk_actor_attempts_infos(
        [t.id for t in tasks],
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        game=game,
        replay_slot=replay_slot,
    )
    gameplay_context_tokens = {
        t.id: issue_gameplay_context(
            task=t, game=game, team=team, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        )
        for t in tasks
    }
    grid_puzzle_data = {}
    for t in tasks:
        if t.task_type != 'grid-puzzle':
            continue
        ai = attempts_info_by_task_id.get(t.id)
        solved = bool(ai and ai.is_solved())
        try:
            grid_puzzle_data[t.id] = public_grid_puzzle_context(
                t, reveal_solution=solved, readonly=solved,
            )
        except GridPuzzleDataError:
            continue
    wall_max_points_meta_by_task_id = {}
    for t in tasks:
        if t.task_type != 'wall':
            continue
        try:
            wall = t.get_wall()
            base_max = getattr(wall, 'max_points', None)
            if base_max is None:
                continue
            total = base_max * t.get_points()
            try:
                n_cat = int(getattr(wall, 'n_cat', 0))
                pw = int(getattr(wall, 'points_words', 0))
                pe = int(getattr(wall, 'points_explanation', 0))
                pb = int(getattr(wall, 'points_bonus', 0))
            except Exception:
                n_cat, pw, pe, pb = 0, 0, 0, 0
            base_parts_words = n_cat * pw
            base_parts_expl = n_cat * pe
            base = base_parts_words + base_parts_expl + pb
            mul = t.get_points()
            # total may be Decimal; show without trailing .000
            try:
                total_int = int(total)
                total_str = str(total_int) if total == total_int else str(total).rstrip('0').rstrip('.')
            except Exception:
                total_str = str(total).rstrip('0').rstrip('.')
            if mul == 1:
                title = '{total} = {w} за состав категорий + {e} за смысл категорий + {b} за полное решение'.format(
                    total=total_str,
                    w=base_parts_words,
                    e=base_parts_expl,
                    b=pb,
                )
            else:
                try:
                    mul_int = int(mul)
                    mul_str = str(mul_int) if mul == mul_int else str(mul).rstrip('0').rstrip('.')
                except Exception:
                    mul_str = str(mul).rstrip('0').rstrip('.')
                # Расписываем подробно: (w + e + b) × mul = w*mul + e*mul + b*mul
                try:
                    w_mul = base_parts_words * mul
                    e_mul = base_parts_expl * mul
                    b_mul = pb * mul
                    w_mul_int = int(w_mul)
                    e_mul_int = int(e_mul)
                    b_mul_int = int(b_mul)
                    w_mul_str = str(w_mul_int) if w_mul == w_mul_int else str(w_mul).rstrip('0').rstrip('.')
                    e_mul_str = str(e_mul_int) if e_mul == e_mul_int else str(e_mul).rstrip('0').rstrip('.')
                    b_mul_str = str(b_mul_int) if b_mul == b_mul_int else str(b_mul).rstrip('0').rstrip('.')
                except Exception:
                    w_mul_str = str(base_parts_words)
                    e_mul_str = str(base_parts_expl)
                    b_mul_str = str(pb)
                title = (
                    '{total} = ({w} за состав + {e} за смысл + {b} за бонус) × {mul} '
                    '= {w2} + {e2} + {b2}'
                ).format(
                    total=total_str,
                    w=base_parts_words,
                    e=base_parts_expl,
                    b=pb,
                    mul=mul_str,
                    w2=w_mul_str,
                    e2=e_mul_str,
                    b2=b_mul_str,
                )
            wall_max_points_meta_by_task_id[t.id] = {'total': total, 'title': title}
        except Exception:
            pass
    likes_meta_by_task_id = {}
    for t in tasks:
        likes_meta_by_task_id[t.id] = {
            # Показываем сумму КОМАНДНЫХ + ЛИЧНЫХ лайков/дизлайков.
            'likes': Like.manager.get_total_likes(t),
            'dislikes': Like.manager.get_total_dislikes(t),
            # Лайк/дизлайк ставим в зависимости от режима.
            'liked': Like.manager.actor_has_like(t, team=team, user=user, anon_key=anon_key),
            'disliked': Like.manager.actor_has_dislike(t, team=team, user=user, anon_key=anon_key),
        }
    replacements_lines_data = {}
    html_forms_data = {}
    word_salad_data = {}
    if placement is None and getattr(task_group, 'pk', None) and getattr(game, 'pk', None):
        placement = (
            GameTaskGroup.objects
            .filter(game_id=game.pk, task_group_id=task_group.pk)
            .only('number', 'name')
            .first()
        )
    for t in tasks:
        if t.task_type == 'replacements_lines':
            parsed = parse_replacements_lines_text(t.text, (t.checker_data or '').strip() or None)
            n_lines = len(parsed['left_lines'])
            line_solved = [False] * n_lines
            line_attempts = [0] * n_lines
            answers_by_line = parsed.get('answers', [])
            line_done = [False] * n_lines
            solved_slots_from_state = {}
            ai = attempts_info_by_task_id.get(t.id)
            hint_attempts = ai.hint_attempts if ai else []
            if ai and ai.attempts:
                for a in ai.attempts:
                    try:
                        p = json.loads(a.text)
                        idx = int(p.get('line_index', -1))
                        if 0 <= idx < n_lines:
                            line_attempts[idx] += 1
                    except (ValueError, TypeError):
                        pass
            # ChainTaskState is authoritative. Do not reconstruct a solved
            # line by combining slots from different attempts.
            state = _replacements_current_state(
                t, game=game, team=team, user=user, anon_key=anon_key,
                mode=mode, replay_slot=replay_slot, attempts_info=ai,
            )
            solved_slots_from_state = replacements_solved_slots_from_state(
                state, parsed,
            )
            for i in range(n_lines):
                solved = solved_slots_from_state.get(i, set())
                line_done[i] = bool(answers_by_line[i]) and len(solved) == len(answers_by_line[i])
            slot_counts = [len(answers_by_line[i]) for i in range(n_lines)]
            replacements_lines_data[t.id] = {
                'parsed': parsed,
                'line_solved': line_solved,
                'line_done': line_done,
                'line_attempts': line_attempts,
                'slot_correct': [
                    [j in solved_slots_from_state.get(i, set()) for j in range(len(answers_by_line[i]))]
                    for i in range(n_lines)
                ],
                'n_lines': n_lines,
                'slot_counts': slot_counts,
                'max_attempts': t.get_max_attempts(),
                'max_points_total': t.get_results_max_points(),
            }
        elif t.task_type == 'html_forms':
            ai = attempts_info_by_task_id.get(t.id)
            html_forms_data[t.id] = {
                'html': render_html_forms_task(
                    request,
                    t,
                    ai,
                    gameplay_context_tokens.get(t.id, ''),
                    game=game,
                    new_ui=True,
                ),
                'max_points_total': t.get_results_max_points(),
            }
    raddle_data = {}
    daily_board_context_key_by_task_id = {}
    daily_board_template_by_task_id = {}
    daily_board_wrapper_by_task_id = {}
    daily_board_adapter_by_task_id = {}
    word_salad_offer_share = ''
    if game.id == WORD_SALAD_GAME_ID:
        from games.models import WordSaladOffer

        offer = (
            WordSaladOffer.objects
            .filter(task_group_id=task_group.pk)
            .only('share_hash')
            .first()
        )
        word_salad_offer_share = offer.share_hash if offer is not None else ''
    board_data_by_context = {
        'word_salad': word_salad_data,
        'raddle': raddle_data,
    }
    for t in tasks:
        adapter = get_daily_board_adapter(
            t.task_type,
            game_id=getattr(game, 'id', None),
        )
        if adapter is None:
            continue
        ai = attempts_info_by_task_id.get(t.id)
        share_title = None
        if adapter.context_key == 'raddle' and str(getattr(game, 'id', '')) != LADDER_GAME_ID:
            share_title = raddle_share_title(game, placement.number, t.number)
        data = adapter.prepare(
            game=game,
            task=t,
            placement=placement,
            attempts_info=ai,
            team=team,
            user=user,
            anon_key=anon_key,
            mode=mode,
            replay_slot=replay_slot,
            resolve_chain_state=chain_state_for_actor,
            raddle_ui_state_for_actor=_raddle_ui_state_for_actor,
            share_title=share_title,
        )
        if data is not None:
            # A custom Salad is rendered through the same task template as a
            # scheduled Salad, but its share hash is part of the write
            # authorization contract. Put it in the rendered board payload
            # so the client does not have to reverse-engineer it from a URL.
            if t.task_type == 'word_salad' and word_salad_offer_share:
                data['offer_share'] = word_salad_offer_share
            board_data_by_context.setdefault(adapter.context_key, {})[t.id] = data
            daily_board_context_key_by_task_id[t.id] = adapter.context_key
            daily_board_template_by_task_id[t.id] = adapter.body_template
            daily_board_wrapper_by_task_id[t.id] = adapter.body_wrapper
            daily_board_adapter_by_task_id[t.id] = adapter
    proportions_chips = []
    if task_group.view == 'proportions':
        proportions_chips = build_proportions_chips_for_tasks(tasks)
        for c in proportions_chips:
            tid = c.get('task_id')
            ai = attempts_info_by_task_id.get(tid) if tid is not None else None
            c['hide_from_pool'] = bool(ai and ai.is_solved())
    task_ui_by_task_id = {
        t.id: {
            **_task_ui_descriptor(
                t,
                rld=replacements_lines_data.get(t.id),
                html_forms=html_forms_data.get(t.id),
                rd=raddle_data.get(t.id),
                wall_meta=wall_max_points_meta_by_task_id.get(t.id),
                ws=word_salad_data.get(t.id),
                gp=grid_puzzle_data.get(t.id),
                board_context_key=daily_board_context_key_by_task_id.get(t.id),
                body_template_override=daily_board_template_by_task_id.get(t.id),
                body_wrapper_override=daily_board_wrapper_by_task_id.get(t.id),
                show_attempts_override=(
                    daily_board_adapter_by_task_id.get(t.id).show_attempts
                    if daily_board_adapter_by_task_id.get(t.id) is not None
                    else None
                ),
                show_answer_override=(
                    daily_board_adapter_by_task_id.get(t.id).show_answer
                    if daily_board_adapter_by_task_id.get(t.id) is not None
                    else None
                ),
            ),
            'display_name': task_display_name(game, t, placement=placement),
            'wall': _wall_ui_context(t, attempts_info_by_task_id.get(t.id), mode),
        }
        for t in tasks
    }
    return {
        'attempts_info_by_task_id': attempts_info_by_task_id,
        'wall_max_points_meta_by_task_id': wall_max_points_meta_by_task_id,
        'likes_meta_by_task_id': likes_meta_by_task_id,
        'replacements_lines_data': replacements_lines_data,
        'html_forms_data': html_forms_data,
        'grid_puzzle_data': grid_puzzle_data,
        'proportions_chips': proportions_chips,
        'task_ui_by_task_id': task_ui_by_task_id,
        'gameplay_context_tokens': gameplay_context_tokens,
        'daily_board_contexts': board_data_by_context,
        **{
            '{}_data'.format(context_key): data
            for context_key, data in board_data_by_context.items()
        },
    }



__all__ = [
    '_task_ui_descriptor',
    '_wall_ui_context',
    '_replacements_current_state',
    'build_task_group_task_context_dicts',
]
