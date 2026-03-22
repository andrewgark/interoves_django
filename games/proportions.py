"""Parsing and UI helpers for task_type / view «proportions» (object1 / object2)."""


def parse_proportions_pair(answer):
    """
    Parse stored answer into (left, right) strings.
    Preferred separator is ' / ' (space-slash-space); falls back to first '/'.
    """
    if answer is None:
        return None
    s = str(answer).strip()
    if not s:
        return None
    if ' / ' in s:
        a, b = s.split(' / ', 1)
    elif '/' in s:
        a, b = s.split('/', 1)
    else:
        return None
    a, b = a.strip(), b.strip()
    if not a or not b:
        return None
    return (a, b)


def build_proportions_chips_for_tasks(tasks):
    """
    Flat pool: for each proportions-task, left then right label from its answer pair.
    id стабилен: «{task_pk}_0» / «{task_pk}_1» — не меняется при решении других заданий
    (раньше были 0..n-1 после сортировки и ломали localStorage / перетаскивание при дубликатах подписей).
    Порядок в пуле — сортировка по подписи, затем по task_id.
    """
    chips = []
    for t in tasks:
        if getattr(t, 'task_type', None) != 'proportions':
            continue
        pair = parse_proportions_pair(getattr(t, 'answer', None) or '')
        if not pair:
            continue
        task_pk = getattr(t, 'pk', None)
        if task_pk is None:
            task_pk = getattr(t, 'id', None)
        for side, label in enumerate(pair):
            chips.append({
                'id': '{}_{}'.format(task_pk, side),
                'label': label,
                'task_id': task_pk,
            })
    chips.sort(key=lambda c: (c['label'].lower(), str(c['task_id']), c['id']))
    return chips
