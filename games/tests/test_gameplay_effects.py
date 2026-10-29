"""games.gameplay.effects: started/completed tail shared by every move endpoint."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from games.gameplay import effects
from games.gameplay.moves import MoveContext

GAME = SimpleNamespace(id='g1')
TASK = SimpleNamespace(task_group='tg1')


def ctx(**kw):
    return MoveContext(task=TASK, game=GAME, anon_key='anon', mode='general', **kw)


@patch.object(effects, 'publish_completion_analytics')
@patch.object(effects, 'daily_completion_effects_for_game',
              return_value={'timing': {'ms': 5}, 'analytics_events': ['completed']})
@patch.object(effects, 'complete_logical_game', return_value={'record': 'r', 'created': True})
@patch.object(effects, 'is_task_group_complete', return_value=True)
@patch.object(effects, 'analytics_game_kind', return_value='ladder')
@patch.object(effects, 'register_started_game', return_value=['started'])
class ApplyMoveEffectsTests(SimpleTestCase):
    def test_started_and_completed(self, started, kind, complete, completion, applied, publish):
        result = effects.apply_move_effects(ctx(), started=True, may_complete=True, source='send_attempt')
        self.assertEqual(result.analytics_events, ['started', 'completed'])
        self.assertEqual(result.daily_timing, {'ms': 5})
        self.assertTrue(result.replay_available)
        self.assertEqual(completion.call_args.kwargs['source'], 'send_attempt')
        self.assertEqual(
            completion.call_args.kwargs['actor'],
            {'team': None, 'user': None, 'anon_key': 'anon'},
        )

    def test_move_that_cannot_complete_only_starts(self, started, kind, complete, completion, applied, publish):
        result = effects.apply_move_effects(ctx(), started=True, may_complete=False, source='raddle_assist')
        self.assertEqual(result.analytics_events, ['started'])
        complete.assert_not_called()
        completion.assert_not_called()

    def test_not_started_and_unsolved_group(self, started, kind, complete, completion, applied, publish):
        complete.return_value = False
        result = effects.apply_move_effects(ctx(), started=False, may_complete=True, source='send_attempt')
        self.assertEqual(result.analytics_events, [])
        started.assert_not_called()
        completion.assert_not_called()
        self.assertFalse(result.replay_available)

    def test_replay_does_not_count_a_start_or_offer_another_replay(
        self, started, kind, complete, completion, applied, publish,
    ):
        applied.return_value = {'timing': None, 'analytics_events': []}
        result = effects.apply_move_effects(
            ctx(replay_slot=SimpleNamespace(pk=1)), started=True, may_complete=True, source='send_attempt',
        )
        started.assert_not_called()
        self.assertFalse(result.replay_available)
        self.assertIsNone(result.daily_timing)

    def test_game_outside_analytics_is_never_completed(
        self, started, kind, complete, completion, applied, publish,
    ):
        kind.return_value = None
        effects.apply_move_effects(ctx(), started=True, may_complete=True, source='send_attempt')
        complete.assert_not_called()
