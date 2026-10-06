from django.test import TestCase
from django.urls import reverse
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

from games.models import Attempt, ChainTaskState, HintAttempt, PlayerCompletedGame, ReplaySlot
from games.tutorials.ladder import LADDER_TUTORIAL


class LadderTutorialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.get_current()
        for provider in ('google', 'yandex', 'vk'):
            app = SocialApp.objects.create(
                provider=provider, name=provider, client_id='test', secret='test',
            )
            app.sites.add(site)

    def test_anonymous_route_is_public_and_matches_production_raddle_markup(self):
        response = self.client.get(reverse('ui_ladder_tutorial'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мини-Лесенка')
        self.assertContains(response, 'ПЕРВАЯ')
        self.assertContains(response, 'ПЯТАЯ')
        self.assertContains(response, 'ЗАПЯТАЯ')
        self.assertContains(response, 'ТОЧКА')
        self.assertContains(response, 'ЛАСТОЧКА')
        self.assertContains(response, 'data-tutorial-target="word-list"')
        self.assertContains(response, 'data-tutorial-target="clues-unused"')
        self.assertContains(response, 'data-tutorial-target="answer-input"')
        self.assertContains(response, 'data-tutorial-game="ladder"')
        self.assertContains(response, 'data-disable-track="1"')
        self.assertNotContains(response, '17.')
        self.assertNotContains(response, '0 баллов')
        self.assertContains(response, 'data-ladder-tutorial-complete')
        self.assertNotContains(response, '/send_attempt/')
        self.assertNotContains(response, '/send_raddle_assist/')
        self.assertNotContains(response, 'name="gameplay_context"')

    def test_adapter_is_deterministic_short_ladder_config(self):
        payload = LADDER_TUTORIAL.payload()

        self.assertEqual(payload['words'], ('ПЕРВАЯ', 'ПЯТАЯ', 'ЗАПЯТАЯ', 'ТОЧКА', 'ЛАСТОЧКА'))
        self.assertEqual(payload['lengths'], (6, 5, 7, 5, 8))
        self.assertEqual(payload['initial_solved_indices'], (0, 4))
        self.assertEqual(payload['initial_used_hint_indices'], ())
        self.assertEqual(payload['steps']['first']['answer'], 'ПЯТАЯ')
        self.assertEqual(payload['steps']['second']['answer'], 'ТОЧКА')
        self.assertEqual(payload['steps']['lower_pair']['word_indices'], (3, 4))
        self.assertIn('ПЕРВАЯ', payload['used_hint_display'][0])
        self.assertIn('ПЯТАЯ', payload['used_hint_display'][0])
        ui = LADDER_TUTORIAL.ui_context()
        self.assertEqual(
            [row['index'] for row in ui['rows'] if row['is_solved']], [0, 4],
        )
        self.assertTrue(ui['rows'][1]['is_playable'])
        self.assertTrue(ui['rows'][3]['is_playable'])
        self.assertTrue(ui['rows'][2]['is_draftable'])
        self.assertEqual([hint['index'] for hint in ui['used_hints']], [])

    def test_get_does_not_touch_production_gameplay_models(self):
        before = {
            'attempts': Attempt.manager.count(),
            'hints': HintAttempt.objects.count(),
            'chain': ChainTaskState.objects.count(),
            'completed': PlayerCompletedGame.objects.count(),
            'replays': ReplaySlot.objects.count(),
        }

        self.client.get('/ladder/tutorial/')

        after = {
            'attempts': Attempt.manager.count(),
            'hints': HintAttempt.objects.count(),
            'chain': ChainTaskState.objects.count(),
            'completed': PlayerCompletedGame.objects.count(),
            'replays': ReplaySlot.objects.count(),
        }
        self.assertEqual(after, before)
