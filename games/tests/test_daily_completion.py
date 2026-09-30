from unittest import TestCase
from unittest.mock import Mock

from games.daily.completion import daily_completion_effects


class DailyCompletionEffectsTests(TestCase):
    def test_completion_publishes_timing_and_analytics_once(self):
        publish = Mock(return_value=['completed'])

        effects = daily_completion_effects(
            {'timing': {'elapsed': 12}},
            replay_slot=None,
            publish_analytics=publish,
            analytics_kwargs={'game': 'game'},
        )

        self.assertEqual(effects['timing'], {'elapsed': 12})
        self.assertEqual(effects['analytics_events'], ['completed'])
        publish.assert_called_once_with(game='game')

    def test_replay_completion_keeps_timing_but_suppresses_analytics(self):
        publish = Mock(return_value=['must not publish'])

        effects = daily_completion_effects(
            {'timing': {'elapsed': 12}},
            replay_slot='replay',
            publish_analytics=publish,
            analytics_kwargs={},
        )

        self.assertEqual(effects['timing'], {'elapsed': 12})
        self.assertEqual(effects['analytics_events'], [])
        publish.assert_not_called()

    def test_missing_completion_has_no_effects(self):
        publish = Mock()

        effects = daily_completion_effects(
            None,
            replay_slot=None,
            publish_analytics=publish,
            analytics_kwargs={},
        )

        self.assertEqual(effects, {'timing': None, 'analytics_events': []})
        publish.assert_not_called()
