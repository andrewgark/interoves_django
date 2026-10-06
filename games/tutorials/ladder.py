"""Immutable, isolated demo payload for the short Ladder tutorial."""

import json
from types import SimpleNamespace

from games.raddle import build_raddle_ui_context, parse_raddle_data, used_clue_display


LADDER_TUTORIAL_VERSION = 'v6'

_WORDS = ('ПЕРВАЯ', 'ПЯТАЯ', 'ЗАПЯТАЯ', 'ТОЧКА', 'ЛАСТОЧКА')
_LENGTHS = tuple(len(word) for word in _WORDS)
_HINTS = (
    '____ и ... буквы алфавита — это А и Д',
    'Знак препинания, заканчивающийся на ____',
    '____ — это ... с хвостиком',
    'Птица, заканчивающаяся на ____',
)


class LadderTutorialAdapter:
    task_id = 'ladder-tutorial-demo'
    # The endpoints define the puzzle; every word between them starts blank.
    initial_solved_indices = (0, 4)
    initial_used_hint_indices = ()

    def task(self):
        checker_data = json.dumps({
            'lengths': list(_LENGTHS), 'hints': list(_HINTS),
            'words': list(_WORDS),
            'raddle_assist': {'enabled': True, 'fractions': [1, 0.5, 0]},
        }, ensure_ascii=False)
        return SimpleNamespace(
            id=self.task_id, number='17', task_type='raddle',
            text='Короткая Лесенка', answer='\n'.join(_WORDS),
            checker_data=checker_data,
            attempt_revision='ladder-tutorial-v6', tags={},
        )

    def parsed(self):
        return parse_raddle_data(self.task())

    def initial_state(self):
        return {
            'solved_indices': list(self.initial_solved_indices),
            'used_hints': list(self.initial_used_hint_indices),
            'assist_tier': {}, 'drafts': {},
        }

    def ui_context(self, state=None):
        return build_raddle_ui_context(
            self.parsed(), state or self.initial_state(), mode='daily',
        )

    def payload(self):
        return {
            'version': LADDER_TUTORIAL_VERSION, 'game': 'ladder',
            'task_id': self.task_id, 'words': _WORDS, 'lengths': _LENGTHS,
            'hints': _HINTS,
            # Use the same renderer as production's used-clue list so newly
            # solved demo edges get their solved-word chips/colors, not the
            # stale unused-clue markup captured before the answer.
            'used_hint_display': [
                used_clue_display(hint, index, _WORDS, html=True)
                for index, hint in enumerate(_HINTS)
            ],
            'initial_solved_indices': self.initial_solved_indices,
            'initial_used_hint_indices': self.initial_used_hint_indices,
            'steps': {
                'first': {'hint_index': 0, 'word_index': 1, 'answer': _WORDS[1]},
                'second': {'hint_index': 3, 'word_index': 3, 'answer': _WORDS[3]},
                'lower_pair': {'word_indices': (3, 4), 'hint_index': 3},
            },
        }


LADDER_TUTORIAL = LadderTutorialAdapter()
