from datetime import timedelta

from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup


class DailyTaskFooterPartialTests(SimpleTestCase):
    def test_project_task_group_context_does_not_require_daily_fallbacks(self):
        html = render_to_string(
            'new/partials/daily_task_footer.html',
            {
                'task_group_results_url': '/glowbyte/games/example/1/results/',
                'task_group_results_allowed': True,
                'task_group_pager_label': 'Десяточка 11 в Glowbyte',
                'task_group_pager_aria_label': 'Переход между заданиями',
                'next_task_group_url': '/glowbyte/games/example/2/',
                'next_task_group_number': '2',
            },
        )

        self.assertIn('href="/glowbyte/games/example/1/results/"', html)
        self.assertIn('href="/glowbyte/games/example/2/"', html)
        self.assertIn('Десяточка 11 в Glowbyte №2', html)

    def test_daily_context_does_not_require_task_group_fallbacks(self):
        html = render_to_string(
            'new/partials/daily_task_footer.html',
            {
                'daily_results_url': '/ladder/1/results/',
                'daily_results_allowed': True,
                'daily_results_label': 'Результаты слов',
                'daily_game_label': 'Лесенка',
                'daily_pager_aria_label': 'Переход между лесенками',
                'prev_task_group_url': '/ladder/0/',
                'prev_task_group_number': '0',
            },
        )

        self.assertIn('href="/ladder/1/results/"', html)
        self.assertIn('Результаты слов', html)
        self.assertIn('Лесенка №0', html)


class ProjectTaskGroupFooterIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        site, _ = Site.objects.get_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'test'},
        )
        for provider, name in (('google', 'Google'), ('vk', 'VK')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)

        now = timezone.now()
        game = Game.objects.create(
            id='glowbyte_des_11',
            name='Десяточка 11 в Glowbyte',
            outside_name='Десяточка 11 в Glowbyte',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            is_registrable=False,
            requires_ticket=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            project_id='glowbyte',
        )
        task_group = TaskGroup.objects.create(label='Палиндромы')
        GameTaskGroup.objects.create(
            game=game,
            task_group=task_group,
            number='1',
            name='Палиндромы',
        )
        Task.objects.create(
            task_group=task_group,
            number='1',
            text='Тестовое задание',
            answer='ответ',
            checker=checker,
            points=1,
        )

    def test_project_scoped_task_group_renders(self):
        response = self.client.get('/glowbyte/games/glowbyte_des_11/1/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Десяточка 11 в Glowbyte')
        self.assertContains(response, 'Тестовое задание')
