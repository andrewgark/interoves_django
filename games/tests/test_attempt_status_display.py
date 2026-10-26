import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.site.http.tags.filters import attempts_with_status


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

    def test_raddle_given_ends_are_not_progress_of_a_wrong_guess(self):
        rendered = attempts_with_status([
            _attempt(
                'Partial',
                task_type='raddle',
                points=0,
                time=1,
                text=json.dumps({'word_index': 4, 'word': 'КОТЯ'}),
                state=json.dumps({'solved_indices': [0, 10]}),
            ),
        ])

        self.assertEqual(rendered[0]['mark'], 'wrong')

    def test_word_salad_find_is_partial_when_hints_keep_the_score_flat(self):
        def salad(points, time, solved, *, hints=0, action='solve'):
            return _attempt(
                'Partial',
                task_type='word_salad',
                points=points,
                time=time,
                text=json.dumps({'action': action}),
                state=json.dumps({
                    'solved_indices': solved,
                    'found_rare': [],
                    'found_extra': [],
                    'hint_counts': {'1': hints} if hints else {},
                }),
            )

        rendered = attempts_with_status([
            salad(1, 1, [0]),
            salad('0.5', 2, [0], hints=1, action='hint'),
            salad(0, 3, [0], hints=2, action='hint'),
            salad(1, 4, [0, 1]),
        ])

        self.assertEqual(
            [item['mark'] for item in rendered],
            ['partial', 'wrong', 'wrong', 'partial'],
        )

    def test_replacements_cell_is_partial_before_the_line_scores(self):
        def line(time, slots, solved_lines):
            return _attempt(
                'Partial',
                task_type='replacements_lines',
                points=0,
                time=time,
                state=json.dumps({
                    'solved_slots': slots,
                    'solved_lines': solved_lines,
                    'total': len(solved_lines),
                }),
            )

        rendered = attempts_with_status([
            line(1, {'0': [0]}, []),
            line(2, {'0': [0]}, []),
            line(3, {'0': [0, 1]}, [0]),
        ])

        self.assertEqual(
            [item['mark'] for item in rendered],
            ['partial', 'wrong', 'partial'],
        )

    def test_html_forms_new_key_is_partial_when_points_stay_flat(self):
        def form(time, keys):
            return _attempt(
                'Partial',
                task_type='html_forms',
                points=0,
                time=time,
                state=json.dumps({'solved_keys': keys}),
            )

        rendered = attempts_with_status([
            form(1, ['a']),
            form(2, ['a']),
            form(3, ['a', 'b']),
        ])

        self.assertEqual(
            [item['mark'] for item in rendered],
            ['partial', 'wrong', 'partial'],
        )

    def test_alphabetty_misses_stay_wrong_as_the_guess_list_grows(self):
        rendered = attempts_with_status([
            _attempt(
                'Partial', task_type='alphabetty', points=0, time=1,
                state=json.dumps({'guesses': ['АИСТ'], 'won': False}),
            ),
            _attempt(
                'Partial', task_type='alphabetty', points=0, time=2,
                state=json.dumps({'guesses': ['АИСТ', 'БЕРКУТ'], 'won': False}),
            ),
        ])

        self.assertEqual([item['mark'] for item in rendered], ['wrong', 'wrong'])
