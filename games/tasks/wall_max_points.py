"""Wall task max-points breakdown for task-group UI (title tooltip)."""


def _decimal_display(value) -> str:
    try:
        as_int = int(value)
        if value == as_int:
            return str(as_int)
    except (TypeError, ValueError):
        pass
    return str(value).rstrip('0').rstrip('.')


def wall_max_points_meta_for_task(task) -> dict | None:
    """Return ``{'total', 'title'}`` for a wall task, or None if unavailable."""
    if task.task_type != 'wall':
        return None
    try:
        wall = task.get_wall()
        base_max = getattr(wall, 'max_points', None)
        if base_max is None:
            return None
        total = base_max * task.get_points()
        try:
            n_cat = int(getattr(wall, 'n_cat', 0))
            pw = int(getattr(wall, 'points_words', 0))
            pe = int(getattr(wall, 'points_explanation', 0))
            pb = int(getattr(wall, 'points_bonus', 0))
        except (TypeError, ValueError):
            n_cat, pw, pe, pb = 0, 0, 0, 0
        base_parts_words = n_cat * pw
        base_parts_expl = n_cat * pe
        mul = task.get_points()
        total_str = _decimal_display(total)
        if mul == 1:
            title = f'{total_str} = {base_parts_words} за состав категорий + {base_parts_expl} за смысл категорий + {pb} за полное решение'
        else:
            mul_str = _decimal_display(mul)
            try:
                w_mul = base_parts_words * mul
                e_mul = base_parts_expl * mul
                b_mul = pb * mul
                w_mul_str = _decimal_display(w_mul)
                e_mul_str = _decimal_display(e_mul)
                b_mul_str = _decimal_display(b_mul)
            except (TypeError, ValueError):
                w_mul_str = str(base_parts_words)
                e_mul_str = str(base_parts_expl)
                b_mul_str = str(pb)
            title = (
                f'{total_str} = ({base_parts_words} за состав + {base_parts_expl} за смысл + {pb} за бонус) × {mul_str} '
                f'= {w_mul_str} + {e_mul_str} + {b_mul_str}'
            )
        return {'total': total, 'title': title}
    except Exception:
        return None


def wall_max_points_meta_by_task_id(tasks) -> dict:
    """Collect wall max-points meta for wall tasks in a task group."""
    out = {}
    for t in tasks:
        meta = wall_max_points_meta_for_task(t)
        if meta is not None:
            out[t.id] = meta
    return out
