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
    Flat pool: for each proportions-task in order, append left then right label.
    Each chip has a unique integer id (stable for drag state / localStorage).
    task_id — задание, из ответа которого взят объект (два чипа на одно задание).
    """
    chips = []
    idx = 0
    for t in tasks:
        if getattr(t, 'task_type', None) != 'proportions':
            continue
        pair = parse_proportions_pair(getattr(t, 'answer', None) or '')
        if not pair:
            continue
        task_pk = getattr(t, 'pk', None)
        if task_pk is None:
            task_pk = getattr(t, 'id', None)
        for label in pair:
            chips.append({'id': idx, 'label': label, 'task_id': task_pk})
            idx += 1
    return chips
