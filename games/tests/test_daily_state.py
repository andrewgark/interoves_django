from types import SimpleNamespace
from unittest import TestCase

from games.daily.state import latest_daily_state


class DailyStateTests(TestCase):
    def test_chain_state_has_priority_over_attempts(self):
        chain = SimpleNamespace(state='chain')
        attempts = SimpleNamespace(
            attempts=[SimpleNamespace(state='old'), SimpleNamespace(state='latest')]
        )

        state = latest_daily_state(
            'task',
            'game',
            attempts,
            default_state='default',
            decode_state=lambda value: value.upper(),
            resolve_chain_state=lambda task, game, **kwargs: chain,
            chain_state_kwargs={},
        )

        self.assertEqual(state, 'CHAIN')

    def test_latest_attempt_is_used_without_chain_state(self):
        attempts = SimpleNamespace(
            attempts=[SimpleNamespace(state=None), SimpleNamespace(state='latest')]
        )

        state = latest_daily_state(
            'task',
            None,
            attempts,
            default_state='default',
            decode_state=lambda value: value.upper(),
            resolve_chain_state=lambda *args, **kwargs: None,
            chain_state_kwargs={},
        )

        self.assertEqual(state, 'LATEST')

    def test_default_is_used_when_no_state_exists(self):
        state = latest_daily_state(
            'task',
            None,
            SimpleNamespace(attempts=[]),
            default_state='default',
            decode_state=lambda value: value,
            resolve_chain_state=lambda *args, **kwargs: None,
            chain_state_kwargs={},
        )

        self.assertEqual(state, 'default')
