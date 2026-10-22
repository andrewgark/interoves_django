from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import (
    Attempt,
    ChainTaskState,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    TaskGroup,
)
from games.ladder_daily import LADDER_GAME_ID
from games.tests.test_offer_ladder import (
    _ensure_reference_rows,
    _ensure_social_apps,
    _make_ladder_game,
)
from games.tutorials.ladder import (
    LADDER_TUTORIAL_LABEL,
    ensure_ladder_tutorial_task,
    is_ladder_tutorial_task_group,
)


class LadderTutorialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        _ensure_social_apps()
        cls.game = _make_ladder_game()
        cls.user = User.objects.create_user('player', password='x')
        Profile.objects.create(user=cls.user, first_name='Игрок', last_name='Тест')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _attempt(self, task, word_index, word):
        return self.client.post(
            '/send_attempt/{}/'.format(task.pk),
            {'game_id': LADDER_GAME_ID, 'word_index': str(word_index), 'word': word},
            HTTP_X_INTEROVES_PLAY_MODE='personal',
        )

    def test_page_is_the_regular_ladder_play_page_with_popups(self):
        response = self.client.get(reverse('ui_ladder_tutorial'))

        self.assertEqual(response.status_code, 200)
        task = ensure_ladder_tutorial_task()
        self.assertContains(response, 'Обучающая Лесенка')
        self.assertContains(response, 'ПЕРВАЯ')
        self.assertContains(response, 'ЛАСТОЧКА')
        # Same board markup and attempt path as a daily Ladder.
        self.assertContains(response, 'action="/send_attempt/{}/"'.format(task.pk))
        self.assertContains(response, 'data-raddle-auto="1"')
        self.assertContains(response, 'data-raddle-unused-clues')
        self.assertNotContains(response, 'data-tutorial-answer-input')
        # Popup layer on top; the rules modal does not open over it.
        self.assertContains(response, 'data-ladder-tutorial')
        self.assertContains(response, 'ladder-tutorial-data')
        self.assertContains(response, 'data-rules-autoopen="0"')

    def test_task_is_created_once_and_kept_in_sync(self):
        task = ensure_ladder_tutorial_task()
        revision = task.attempt_revision

        again = ensure_ladder_tutorial_task()

        self.assertEqual(again.pk, task.pk)
        self.assertEqual(again.attempt_revision, revision)
        self.assertEqual(TaskGroup.objects.filter(label=LADDER_TUTORIAL_LABEL).count(), 1)
        self.assertTrue(is_ladder_tutorial_task_group(task.task_group))
        self.assertEqual(task.answer.split('\n'), ['ПЕРВАЯ', 'ПЯТАЯ', 'ЗАПЯТАЯ', 'ТОЧКА', 'ЛАСТОЧКА'])

        task.checker_data = '{}'
        task.save()
        synced = ensure_ladder_tutorial_task()
        self.assertIn('ПЯТАЯ', synced.checker_data)

    def test_words_are_checked_by_the_regular_attempt_endpoint(self):
        task = ensure_ladder_tutorial_task()

        wrong = self._attempt(task, 1, 'ПЯТЬЯ').json()
        right = self._attempt(task, 1, 'ПЯТАЯ').json()

        self.assertEqual(wrong['status'], 'ok')
        self.assertFalse(wrong['raddle_correct'])
        self.assertEqual(right['status'], 'ok')
        self.assertTrue(right['raddle_correct'])
        self.assertIn(str(task.pk), right['update_task_html_new'])

    def test_solving_is_not_counted_as_a_played_game(self):
        task = ensure_ladder_tutorial_task()
        self.client.get(reverse('ui_ladder_tutorial'))

        for index, word in ((1, 'ПЯТАЯ'), (3, 'ТОЧКА'), (2, 'ЗАПЯТАЯ')):
            data = self._attempt(task, index, word).json()
            self.assertTrue(data['raddle_correct'], word)
            self.assertFalse(data.get('analytics_events'))
        # The regular result card links back to the tutorial itself.
        self.assertIn('/ladder/tutorial', data['update_task_html_new'][str(task.pk)])

        self.assertFalse(PlayerCompletedGame.objects.filter(task_group=task.task_group).exists())
        self.assertFalse(PlayerStartedGame.objects.filter(task_group=task.task_group).exists())
        self.assertFalse(PlayerAnalyticsState.objects.filter(user=self.user).exclude(activated_at=None).exists())

    def test_every_visit_restarts_the_board(self):
        task = ensure_ladder_tutorial_task()
        self._attempt(task, 1, 'ПЯТАЯ')
        self.assertTrue(Attempt.manager.filter(task=task, user=self.user).exists())

        response = self.client.get(reverse('ui_ladder_tutorial'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attempt.manager.filter(task=task, user=self.user).exists())
        self.assertFalse(ChainTaskState.objects.filter(task=task, user=self.user).exists())
