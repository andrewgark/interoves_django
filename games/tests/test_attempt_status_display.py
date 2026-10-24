import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.templatetags.filters import attempts_with_status


def _attempt(status, *, task_type='default', points=0, time=0, text='', state=''):
    return SimpleNamespace(
        status=status,
        task=SimpleNamespace(task_type=task_type),
        points=points,
        time=time,
        text=text,
        state=state,
    )


class AttemptsWithStatusTests(SimpleTestCase):
    def test_regular_statuses_have_distinct_marks_and_labels(self):
        attempts = [
            _attempt('Ok', time=1),
            _attempt('Partial', time=2),
            _attempt('Wrong', time=3),
            _attempt('Pending', time=4),
        ]

        rendered = attempts_with_status(attempts)

        self.assertEqual(
            [(item['mark'], item['label']) for item in rendered],
            [
                ('ok', 'верно'),
                ('partial', 'частично'),
                ('wrong', 'неверно'),
                ('pending', 'проверяется'),
            ],
        )

    def test_chain_attempts_still_use_points_gain_as_progress(self):
        attempts = [
            _attempt('Partial', task_type='wall', points=1, time=1),
            _attempt('Partial', task_type='wall', points=1, time=2),
            _attempt('Ok', task_type='wall', points=3, time=3),
        ]

        rendered = attempts_with_status(attempts)

        self.assertEqual([item['mark'] for item in rendered], ['partial', 'wrong', 'ok'])

    def test_pending_chain_attempt_is_not_presented_as_wrong(self):
        rendered = attempts_with_status([
            _attempt('Pending', task_type='wall', points=0, time=1),
        ])

        self.assertEqual(rendered[0]['mark'], 'pending')
        self.assertEqual(rendered[0]['label'], 'проверяется')

    def test_raddle_answer_hint_solve_is_partial_when_points_stay_flat(self):
        def raddle(word, solved, time):
            return _attempt(
                'Partial',
                task_type='raddle',
                points=5,
                time=time,
                text=json.dumps({'word_index': 4, 'word': word}),
                state=json.dumps({'solved_indices': solved}),
            )

        rendered = attempts_with_status([
            _attempt(
                'Partial',
                task_type='raddle',
                points=5,
                time=0,
                text=json.dumps({'word_index': 3, 'word': 'КОШКА'}),
                state=json.dumps({'solved_indices': [0, 1, 2, 3]}),
            ),
            raddle('КОТЯ', [0, 1, 2, 3], 1),
            raddle('КИСА', [0, 1, 2, 3, 4], 2),
            raddle('КИСА', [0, 1, 2, 3, 4], 3),
        ])

        self.assertEqual(
            [item['mark'] for item in rendered],
            ['partial', 'wrong', 'partial', 'wrong'],
        )
        self.assertEqual(rendered[2]['label'], 'частично')
