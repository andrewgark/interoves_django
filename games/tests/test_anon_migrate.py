from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from unittest.mock import patch

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    Hint,
    HintAttempt,
    HTMLPage,
    Profile,
    Project,
    StatisticsEvent,
    Task,
    TaskGroup,
)


class AnonMigrateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='anon_migrate_test',
                name='Anon migrate',
                author='test',
                author_extra='',
                project_id='main',
                is_ready=True,
            )
            cls.tg = TaskGroup.objects.create(label='anon_migrate_tg')
            GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.tg, number=1, name='G1',
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                checker=CheckerType.objects.get(pk='equals'),
                points=1,
                answer='ok',
            )
            cls.hint = Hint.objects.create(
                task=cls.task,
                number='1',
                points_penalty=0,
            )
        cls.user = User.objects.create_user('migrate_user', 'migrate@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='M', last_name='U')
        cls.anon_key = 'anon-migrate-key-1'

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='migrate_user', password='secret'))
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(
                anon_key=self.anon_key,
                task=self.task,
                game=self.game,
                text='a',
                status='Wrong',
            )
            Attempt.manager.create(
                anon_key=self.anon_key,
                task=self.task,
                game=self.game,
                text='ok',
                status='Ok',
            )
            HintAttempt.objects.create(
                anon_key=self.anon_key,
                hint=self.hint,
            )

    def test_migrate_moves_attempts_and_records_statistics_event(self):
        url = reverse('new_migrate_anon_attempts')
        resp = self.client.post(url, {'anon_key': self.anon_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['moved'], 2)
        self.assertEqual(data['moved_hints'], 1)

        self.assertEqual(
            Attempt.manager.filter(user=self.user, task=self.task, anon_key__isnull=True).count(),
            2,
        )
        self.assertEqual(
            Attempt.manager.filter(anon_key=self.anon_key).count(),
            0,
        )
        self.assertEqual(
            HintAttempt.objects.filter(user=self.user, hint=self.hint, anon_key__isnull=True).count(),
            1,
        )

        events = StatisticsEvent.objects.filter(
            kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
            user=self.user,
        )
        self.assertEqual(events.count(), 1)
        payload = events.get().payload
        self.assertEqual(payload['anon_key'], self.anon_key)
        self.assertEqual(payload['moved'], 2)
        self.assertEqual(payload['moved_hints'], 1)

    def test_migrate_count_endpoint(self):
        url = reverse('new_anon_migrate_count')
        resp = self.client.get(url, {'anon_key': self.anon_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['attempts'], 2)
        self.assertFalse(data['show_prompt'])
        # Пример-ссылка ведёт на круг (task group), а не на задание.
        expected_url = reverse('new_task_group', kwargs={
            'game_id': self.game.id,
            'task_group_number': '1',
        })
        self.assertEqual(data['example_url'], expected_url)
        self.assertEqual(data['example_label'], 'G1')

    def test_show_prompt_with_enough_unsolved_attempts(self):
        # Ещё 8 анонимных посылок (в setUp уже есть 2) → всего 10.
        with patch('games.views.track.track_task_change'):
            for _ in range(8):
                Attempt.manager.create(
                    anon_key=self.anon_key,
                    task=self.task,
                    game=self.game,
                    text='x',
                    status='Wrong',
                )
        url = reverse('new_anon_migrate_count')
        data = self.client.get(url, {'anon_key': self.anon_key}).json()
        self.assertEqual(data['attempts'], 10)
        self.assertTrue(data['show_prompt'])
        self.assertIn('example_url', data)

    def test_solved_task_is_not_counted(self):
        # Пользователь уже сдал это задание на OK в личном режиме.
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(
                user=self.user,
                task=self.task,
                game=self.game,
                text='ok',
                status='Ok',
            )
            for _ in range(8):
                Attempt.manager.create(
                    anon_key=self.anon_key,
                    task=self.task,
                    game=self.game,
                    text='x',
                    status='Wrong',
                )
        url = reverse('new_anon_migrate_count')
        data = self.client.get(url, {'anon_key': self.anon_key}).json()
        # Все анонимные посылки на уже сданное задание не учитываются.
        self.assertEqual(data['attempts'], 0)
        self.assertFalse(data['show_prompt'])
        self.assertNotIn('example_url', data)

    def test_empty_migrate_does_not_record_event(self):
        url = reverse('new_migrate_anon_attempts')
        resp = self.client.post(url, {'anon_key': 'no-such-anon'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['moved'], 0)
        self.assertEqual(
            StatisticsEvent.objects.filter(kind=StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED).count(),
            0,
        )
