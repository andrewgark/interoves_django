"""Phase 1 of docs/offline-play-plan.md: rules, the page's client check, verdict audit."""
import html
import json
import re
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import caches
from django.test import override_settings

from games.gameplay.policy import NO_CAPABILITIES, resolve_capabilities
from games.models import OfflinePlayRule, Team
from games.puzzles.core.client_spec import verify_word
from games.puzzles.raddle.puzzle import offline_run
from games.tests.test_client_event_receipts import _ClientEventFixture

SPEC_ATTR = re.compile(r'data-offline-spec="([^"]*)"')


@override_settings(OFFLINE_PLAY_ENABLED=True)
class OfflinePlayPolicyTests(_ClientEventFixture):
    def setUp(self):
        super().setUp()
        caches['offline_play'].clear()

    def caps(self, **actor):
        actor = actor or {'anon_key': self.anon_key}
        return resolve_capabilities(game=self.game, task_type='raddle', mode='general', **actor)

    def test_nothing_without_a_rule(self):
        self.assertEqual(self.caps(), NO_CAPABILITIES)

    def test_global_rule_enables_instant_verdict(self):
        OfflinePlayRule.objects.create(instant_verdict=True)
        self.assertTrue(self.caps().instant_verdict)

    @override_settings(OFFLINE_PLAY_ENABLED=False)
    def test_kill_switch_wins_over_rules(self):
        OfflinePlayRule.objects.create(instant_verdict=True)
        self.assertEqual(self.caps(), NO_CAPABILITIES)

    def test_team_play_stays_online(self):
        OfflinePlayRule.objects.create(instant_verdict=True)
        self.assertEqual(self.caps(team=Team(pk=1)), NO_CAPABILITIES)

    def test_most_specific_rule_wins_even_to_switch_off(self):
        OfflinePlayRule.objects.create(instant_verdict=True)
        OfflinePlayRule.objects.create(game=self.game, task_type='raddle', instant_verdict=False)
        OfflinePlayRule.objects.create(task_type='raddle', instant_verdict=True)
        self.assertFalse(self.caps().instant_verdict)

    def test_rule_for_another_task_type_or_mode_does_not_apply(self):
        OfflinePlayRule.objects.create(task_type='wall', instant_verdict=True)
        OfflinePlayRule.objects.create(mode='tournament', instant_verdict=True)
        self.assertEqual(self.caps(), NO_CAPABILITIES)

    def test_disabled_rule_is_ignored(self):
        OfflinePlayRule.objects.create(enabled=False, instant_verdict=True)
        self.assertEqual(self.caps(), NO_CAPABILITIES)

    def test_capabilities_build_on_each_other(self):
        OfflinePlayRule.objects.create(offline_attempts=True, offline_hints=True, offline_timing=True)
        self.assertEqual(self.caps(), NO_CAPABILITIES)

    def test_rollout_is_stable_per_actor(self):
        OfflinePlayRule.objects.create(instant_verdict=True, rollout_percent=50)
        verdicts = {key: self.caps(anon_key=key).instant_verdict for key in map(str, range(200))}
        self.assertTrue(any(verdicts.values()))
        self.assertFalse(all(verdicts.values()))
        self.assertEqual(verdicts, {key: self.caps(anon_key=key).instant_verdict for key in verdicts})

    def test_saving_a_rule_invalidates_the_cache(self):
        rule = OfflinePlayRule.objects.create(instant_verdict=True)
        self.assertTrue(self.caps().instant_verdict)
        rule.enabled = False
        rule.save()
        self.assertFalse(self.caps().instant_verdict)

    def test_rules_still_apply_when_the_cache_is_down(self):
        OfflinePlayRule.objects.create(instant_verdict=True)
        cache = caches['offline_play']
        with patch.object(cache, 'get', side_effect=ConnectionError) as get, \
                patch.object(cache, 'set', side_effect=ConnectionError), \
                patch.object(cache, 'delete', side_effect=ConnectionError) as delete:
            self.assertTrue(self.caps().instant_verdict)
            OfflinePlayRule.objects.create(task_type='raddle', instant_verdict=False)
            self.assertFalse(self.caps().instant_verdict)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(delete.call_count, 1)


@override_settings(OFFLINE_PLAY_ENABLED=True)
class LadderClientCheckTests(_ClientEventFixture):
    def setUp(self):
        super().setUp()
        caches['offline_play'].clear()

    def _page_spec(self, response):
        match = SPEC_ATTR.search(response['update_task_html_new'][str(self.ladder.id)])
        return None if match is None else json.loads(html.unescape(match.group(1)))

    def test_page_gets_a_spec_that_checks_the_words(self):
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True)
        spec = self._page_spec(self._post_word(1, 'BBB', uuid.uuid4()))
        self.assertIsNotNone(spec)
        self.assertTrue(verify_word(spec, 2, 'ccc'))
        self.assertFalse(verify_word(spec, 2, 'bbb'))
        self.assertNotIn('CCC', json.dumps(spec))

    def test_page_without_a_rule_has_no_spec(self):
        self.assertIsNone(self._page_spec(self._post_word(1, 'BBB', uuid.uuid4())))

    @patch('games.ops.telegram.bot.notify.notify_admin_offline_play')
    def test_divergent_client_verdict_is_reported_once_per_task(self, notify):
        with self.assertLogs('games.offline', 'WARNING') as logs:
            self._post(self.ladder, {'word_index': 1, 'word': 'ZZZ', 'client_verdict': 'Ok'})
            self._post(self.ladder, {'word_index': 2, 'word': 'YYY', 'client_verdict': 'Ok'})
        self.assertEqual(len([line for line in logs.output if 'offline_verdict_divergence' in line]), 2)
        self.assertNotIn('ZZZ', ''.join(logs.output))
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs['kind'], 'verdict_divergence')

    @patch('games.ops.telegram.bot.notify.notify_admin_offline_play')
    def test_matching_or_unknown_client_verdict_is_quiet(self, notify):
        self._post(self.ladder, {'word_index': 1, 'word': 'ZZZ', 'client_verdict': 'Wrong'})
        self._post(self.ladder, {'word_index': 2, 'word': 'CCC', 'client_verdict': 'Ok'})
        self._post(self.ladder, {'word_index': 1, 'word': 'BBB', 'client_verdict': 'yes!'})
        notify.assert_not_called()

    @patch('games.ops.telegram.bot.notify.notify_admin_offline_play')
    def test_broken_checker_data_keeps_the_page_and_alerts(self, notify):
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True)
        with patch('games.gameplay.client_checks.client_spec_for_task', side_effect=ValueError('bad')), \
                self.assertLogs('games.offline', 'ERROR'):
            response = self._post_word(1, 'BBB', uuid.uuid4())
        self.assertTrue(response['raddle_correct'])
        self.assertIsNone(self._page_spec(response))
        self.assertEqual(notify.call_args.kwargs['kind'], 'pack_build_failed')


@override_settings(OFFLINE_PLAY_ENABLED=True)
class LadderOfflinePackEndpointTests(_ClientEventFixture):
    def setUp(self):
        super().setUp()
        caches['offline_play'].clear()

    def _pack(self):
        return self.client.post(
            '/raddle_offline_pack/{}/'.format(self.ladder.id),
            {'game_id': self.game.id, 'anon_key': self.anon_key},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()

    def test_pack_needs_offline_attempts(self):
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True)
        self.assertEqual(self._pack(), {'status': 'disabled'})

    def test_pack_holds_pieces_for_every_reachable_state(self):
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True, offline_attempts=True)
        self._post_word(1, 'BBB', uuid.uuid4())
        data = self._pack()
        self.assertEqual(data['status'], 'ok')
        pack = data['pack']
        self.assertEqual(pack['state']['solved_indices'], [0, 1, 3])
        self.assertEqual(pack['rules']['n_words'], 4)
        self.assertEqual(pack['spec']['kind'], 'raddle')
        self.assertIn('new-task-{}'.format(self.ladder.id), pack['card_html'])
        self.assertIn('data-offline-pack-url', pack['card_html'])
        # Only word 2 is left: its row is playable now and solved later.
        self.assertEqual(
            sorted(key.split('|')[0] for key in pack['rows'][2]), ['playable', 'solved'],
        )
        self.assertIn('2', pack['last_word'])
        self.assertIn('new-raddle-result', pack['result'])
        self.assertIn('data-raddle-unused-clues', pack['sections']['unused'])

    def test_pack_loads_clue_reactions_once(self):
        # Every reachable board shows used clues; their reactions are one load.
        from games.puzzles.raddle import clue_likes

        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True, offline_attempts=True)
        with patch.object(clue_likes, 'clue_reactions_meta', wraps=clue_likes.clue_reactions_meta) as meta:
            self.assertEqual(self._pack()['status'], 'ok')
        self.assertEqual(meta.call_count, 1)

    def test_pack_and_card_name_their_run(self):
        # The official game and its replays share the task revision; the page
        # keeps a pack only for the run it was built for.
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True, offline_attempts=True)
        pack = self._pack()['pack']
        self.assertEqual(pack['run'], 'official')
        self.assertIn('data-offline-run="official"', pack['card_html'])
        run_id = uuid.uuid4()
        self.assertEqual(offline_run(SimpleNamespace(run_id=run_id)), str(run_id))
