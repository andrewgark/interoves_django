from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

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

    def test_anonymous_route_is_public_and_contains_deterministic_demo(self):
        response = self.client.get(reverse('ui_ladder_tutorial'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ГРЯЗЬ')
        self.assertContains(response, 'КНЯЗЬ')
        self.assertContains(response, 'ВВЕРХ')
        self.assertContains(response, 'new-taskcard__meta-bar new-proportions-compact-bar')
        self.assertContains(response, 'new-raddle-task__intro')
        self.assertContains(response, 'new-rules-modal tutorial-shell')
        self.assertContains(response, 'data-disable-track="1"')
        self.assertNotContains(response, '/send_attempt/')
        self.assertNotContains(response, '/send_raddle_assist/')
        self.assertNotContains(response, 'gameplay_context')

    def test_adapter_is_code_first_and_matches_ladder_seven_answers(self):
        payload = LADDER_TUTORIAL.payload()

        self.assertEqual(payload['words'][3:7], ('ВВЕРХ', 'РУКИ', 'ЗОЛОТЫЕ', 'ВОРОТА'))
        self.assertEqual(payload['first_answer'], 'ВВЕРХ')
        self.assertEqual(payload['second_answer'], 'РУКИ')
        self.assertEqual(payload['initial_solved_indices'], (0, 1, 2, 7, 8, 9, 10))

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
