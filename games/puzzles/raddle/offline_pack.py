"""Лесенка без сети: кусочки карточки, из которых страница собирает следующие состояния.

The card is made of slots (task-content/raddle/*.html): a row per word, a
clue per transition, the last-word options and the result. What a slot
renders depends on the state only through its *key* (``slot_keys``), so a
ladder of n words needs O(n) pieces, not one card per reachable state. The
browser computes the same keys (static/js/offline_raddle_pack.js) and
assembles the card from the pieces.

``build_pack`` walks every reachable state but builds a context and
renders only where a slot meets a key it has not seen. A key that maps to
two different pieces is a ``PackConflict``: the key misses something its
slot depends on.

Unused clues are rendered without the focus word: on the page
setRaddleFocus fills it in from data-blank-template, as it does on every
focus change.
"""

from __future__ import annotations

import re

from games.puzzles.raddle.engine import (
    both_neighbors_solved,
    clue_display_for_hint,
    clue_index_for_playable_word,
    last_word_role_for_index,
    playable_word_indices,
    reference_role_for_playable,
    reference_word_for_playable,
)
from games.puzzles.raddle.transition import advance

PACK_VERSION = 1
ROW_TEMPLATE = 'task-content/raddle/row.html'
UNUSED_CLUE_TEMPLATE = 'task-content/raddle/clue_unused.html'
USED_CLUE_TEMPLATE = 'task-content/raddle/clue_used.html'
LAST_WORD_TEMPLATE = 'task-content/raddle/last_word.html'
RESULT_TEMPLATE = 'task-content/raddle/result.html'
UNUSED_SECTION_TEMPLATE = 'task-content/raddle/clues_unused_section.html'
USED_SECTION_TEMPLATE = 'task-content/raddle/clues_used_section.html'


# Django masks the CSRF token differently on every render; any mask is valid.
_CSRF_VALUE_RE = re.compile(r'(name="csrfmiddlewaretoken" value=")[^"]*"')


class PackConflict(Exception):
    """One slot key rendered two different pieces."""


def _focus(state, n):
    solved = set(state.get('solved_indices') or [])
    playable = sorted(i for i in playable_word_indices(state, n) if i not in solved)
    focus = playable[0] if playable else None
    last_word = focus is not None and len(playable) == 1 and both_neighbors_solved(focus, solved, n)
    return solved, set(playable), focus, last_word


def slot_keys(state, n, tiers):
    """The key of every slot of the card in ``state``.

    ``tiers`` maps a word index to its assist tier (fixed while offline).
    Returns ``{'rows': [...], 'clues': [...], 'last_word': index | None,
    'complete': bool}``.
    """
    solved, playable, focus, last_word = _focus(state, n)
    focus_ref = reference_word_for_playable(focus, solved, n) if focus is not None else None
    rows = []
    for i in range(n):
        if i in solved:
            status, ref_role, dual = 'solved', '', False
        elif i in playable:
            status = 'playable'
            ref_role = reference_role_for_playable(i, solved, n) or ''
            dual = both_neighbors_solved(i, solved, n)
        else:
            status, ref_role, dual = 'draft', '', False
        rows.append('|'.join((
            status,
            '1' if i == focus else '0',
            '1' if i == focus_ref else '0',
            ref_role,
            '1' if dual else '0',
            last_word_role_for_index(i, focus, dual_clues=last_word),
        )))
    revealed = set()
    for word_index, tier in tiers.items():
        if tier >= 1:
            clue = clue_index_for_playable_word(word_index, solved, n)
            if clue is not None:
                revealed.add(clue)
    used = set(state.get('used_hints') or [])
    clues = [
        'used' if h in used else 'unused|{}|{}|{}'.format(
            int(h in solved), int(h + 1 in solved), int(h in revealed),
        )
        for h in range(n - 1)
    ]
    return {
        'rows': rows,
        'clues': clues,
        'last_word': focus if last_word else None,
        'complete': len(solved) >= n,
    }


def reachable_states(parsed, base_state):
    """Every state the ladder can reach from ``base_state`` by solving words."""
    n = parsed['n_words']
    solved = set(base_state.get('solved_indices') or [])
    top = 0
    while top + 1 < n and top + 1 in solved:
        top += 1
    bottom = n - 1
    while bottom - 1 > top and bottom - 1 in solved:
        bottom -= 1
    open_words = max(0, bottom - top - 1)
    for from_top in range(open_words + 1):
        state = base_state
        for i in range(top + 1, top + 1 + from_top):
            state = advance(state, parsed, i)
        yield state
        for i in range(bottom - 1, bottom - 1 - (open_words - from_top), -1):
            state = advance(state, parsed, i)
            yield state


def _neutral_clues(ui, parsed):
    """Unused clues without the focus word: the page fills it in itself."""
    solved = {row['index'] for row in ui['rows'] if row['is_solved']}
    for hint in ui.get('unused_hints') or []:
        hint['display_html'] = clue_display_for_hint(
            hint['text'], hint['index'], parsed['words'], solved, html=True,
        )
        hint['display'] = clue_display_for_hint(hint['text'], hint['index'], parsed['words'], solved)


def build_pack(parsed, base_state, *, tiers, make_ui, render, verify=False):
    """Pieces of every slot in every state reachable from ``base_state``.

    ``make_ui(state)`` returns the card context (``rd.ui``) of a state;
    ``render(template, extra_context)`` renders one slot template.
    ``verify`` renders every slot of every state and raises
    ``PackConflict`` on a key with two pieces; it is for tests.
    """
    n = parsed['n_words']
    rows = [{} for _ in range(n)]
    clues = [{} for _ in range(n - 1)]
    last_word = {}
    result = None

    def store(pieces, key, html, slot):
        known = pieces.get(key)
        if known is None:
            pieces[key] = html
        elif _CSRF_VALUE_RE.sub(r'\1"', known) != _CSRF_VALUE_RE.sub(r'\1"', html):
            raise PackConflict('{} key {!r} has two pieces'.format(slot, key))

    for state in reachable_states(parsed, base_state):
        keys = slot_keys(state, n, tiers)
        missing_rows = [i for i, key in enumerate(keys['rows']) if verify or key not in rows[i]]
        missing_clues = [h for h, key in enumerate(keys['clues']) if verify or key not in clues[h]]
        need_last = keys['last_word'] is not None and (verify or keys['last_word'] not in last_word)
        need_result = keys['complete'] and (verify or result is None)
        if not (missing_rows or missing_clues or need_last or need_result):
            continue
        ui = make_ui(state)
        _neutral_clues(ui, parsed)
        rd = {'ui': ui}
        for i in missing_rows:
            store(rows[i], keys['rows'][i], render(ROW_TEMPLATE, {'rd': rd, 'row': ui['rows'][i]}), 'row {}'.format(i))
        hints = {hint['index']: hint for hint in (ui.get('unused_hints') or []) + (ui.get('used_hints') or [])}
        for h in missing_clues:
            hint = hints.get(h)
            if hint is None:
                # In the last-word state unused clues live in the options block.
                continue
            template = USED_CLUE_TEMPLATE if keys['clues'][h] == 'used' else UNUSED_CLUE_TEMPLATE
            store(clues[h], keys['clues'][h], render(template, {'rd': rd, 'hint': hint}), 'clue {}'.format(h))
        if need_last:
            html = render(LAST_WORD_TEMPLATE, {'rd': rd})
            store(last_word, keys['last_word'], html, 'last word')
        if need_result:
            html = render(RESULT_TEMPLATE, {'rd': rd})
            if result is not None and verify and result != html:
                raise PackConflict('result has two pieces')
            result = html

    return {
        'v': PACK_VERSION,
        'n_words': n,
        'tiers': {str(index): tier for index, tier in tiers.items()},
        'unused_order': sorted(range(n - 1), key=lambda h: parsed['hints'][h].lower()),
        'rows': rows,
        'clues': clues,
        'last_word': {str(k): html for k, html in last_word.items()},
        'result': result,
        'sections': {
            'unused': render(UNUSED_SECTION_TEMPLATE, {'unused_hints': []}),
            'used': render(USED_SECTION_TEMPLATE, {'used_hints': []}),
        },
    }
