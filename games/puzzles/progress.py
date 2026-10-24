"""Normalizers for a chain attempt's solved-set signature."""


def _int_tuple(value):
    if not isinstance(value, list):
        return ()
    numbers = []
    for item in value:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(numbers)))


def _text_tuple(value):
    if not isinstance(value, list):
        return ()
    return tuple(sorted(str(item) for item in value))


def _wall_groups(value):
    if not isinstance(value, list):
        return ()
    groups = []
    for group in value:
        if isinstance(group, list):
            groups.append(tuple(str(item) for item in group))
        else:
            groups.append((str(group),))
    return tuple(sorted(groups))


def _slot_pairs(value):
    if not isinstance(value, dict):
        return ()
    pairs = []
    for line, indexes in value.items():
        pairs.append((str(line), _int_tuple(indexes if isinstance(indexes, list) else [])))
    return tuple(sorted(pairs))
