"""Immutable, isolated demo payload for the Ladder tutorial.

The requested tutorial chain is ГРЯЗЬ → … → КНЯЗЬ. The matching local
production task data is copied here as code-first config; this adapter never
loads or mutates a production task or gameplay state at request time.
"""

import json
from types import SimpleNamespace

from games.raddle import build_raddle_ui_context, parse_raddle_data


LADDER_TUTORIAL_VERSION = 'v3'

_WORDS = (
    'ГРЯЗЬ', 'УДАРИТЬ', 'ПАЛЕЦ', 'ВВЕРХ', 'РУКИ', 'ЗОЛОТЫЕ',
    'ВОРОТА', 'ВРАТАРЬ', 'АКИНФЕЕВ', 'ИГОРЬ', 'КНЯЗЬ',
)
_LENGTHS = (5, 7, 5, 5, 4, 7, 6, 7, 8, 5, 5)
_HINTS = (
    '... в ____ лицом',
    '... о ... не ____',
    '____ ... - одобрение',
    '... ____ - поп-группа',
    'У мастера ... ____',
    '____ ... - в Сан-Франциско',
    'Он защищает ____',
    'Одиннадцатикратный "____ года"',
    '____ - а как его зовут?',
    'Опера "... ____"',
)


class LadderTutorialAdapter:
    task_id = 'ladder-tutorial-demo'
    initial_solved_indices = (0, 1, 8, 9, 10)
    initial_used_hint_indices = (0, 7, 8, 9)

    def task(self):
        checker_data = json.dumps({
            'lengths': list(_LENGTHS), 'hints': list(_HINTS),
            'words': list(_WORDS),
            'raddle_assist': {'enabled': True, 'fractions': [1, 0.5, 0]},
        }, ensure_ascii=False)
        return SimpleNamespace(
            id=self.task_id, number='17', task_type='raddle',
            text='Лесенка #17', answer='\n'.join(_WORDS),
            checker_data=checker_data,
            attempt_revision='ladder-tutorial-v3', tags={},
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
            'initial_solved_indices': self.initial_solved_indices,
            'initial_used_hint_indices': self.initial_used_hint_indices,
            'steps': {
                # Word at index 2 is reached from the second solved word.
                'first': {'hint_index': 1, 'word_index': 2, 'answer': _WORDS[2]},
                'second': {'hint_index': 2, 'word_index': 3, 'answer': _WORDS[3]},
                'lower_pair': {'word_indices': (9, 10), 'hint_index': 9},
            },
        }


LADDER_TUTORIAL = LadderTutorialAdapter()
