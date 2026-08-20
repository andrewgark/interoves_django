from types import SimpleNamespace

from django.test import SimpleTestCase

from games.templatetags.filters import attempts_with_status


def _attempt(status, *, task_type='default', points=0, time=0):
    return SimpleNamespace(
        status=status,
        task=SimpleNamespace(task_type=task_type),
        points=points,
        time=time,
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
