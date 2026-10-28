"""The old interface is gone: /old/… redirects to the new UI or answers 410."""
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase

from games.models import Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup


class OldUiRetiredTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
        for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)
        game = Game.objects.create(id='old_ui_main', name='Main', author='a', project_id='main')
        tg = TaskGroup.objects.create(label='old_ui_tg')
        GameTaskGroup.objects.create(game=game, task_group=tg, number='2', name='Round')
        Task.objects.create(task_group=tg, number='1', text='t')

    def assertPermanentRedirectsTo(self, source, target):
        response = self.client.get(source)
        self.assertEqual(response.status_code, 301, source)
        self.assertEqual(response['Location'], target)

    def test_old_pages_redirect_to_new_ui(self):
        self.assertPermanentRedirectsTo('/old/', '/')
        self.assertPermanentRedirectsTo('/old/main/', '/')
        self.assertPermanentRedirectsTo('/old/glowbyte/', '/glowbyte/')
        self.assertPermanentRedirectsTo('/old/games/old_ui_main/', '/games/old_ui_main/')
        self.assertPermanentRedirectsTo('/old/games/old_ui_main/2', '/games/old_ui_main/2/')
        self.assertPermanentRedirectsTo('/old/results/old_ui_main/', '/games/old_ui_main/results/')
        self.assertPermanentRedirectsTo('/old/create_team/', '/team/')
        self.assertPermanentRedirectsTo('/old/request_ticket/', '/pay/')

    def test_old_task_deep_link_opens_its_task_group(self):
        self.assertPermanentRedirectsTo('/games/old_ui_main/2/1', '/games/old_ui_main/2/')
        self.assertPermanentRedirectsTo('/old/games/old_ui_main/2/1?x=1', '/games/old_ui_main/2/?x=1')

    def test_old_only_endpoints_are_gone(self):
        for path in ('/old/total_results/main/', '/old/send_attempt/1/', '/old/like_dislike/1/', '/old/anything'):
            self.assertEqual(self.client.get(path).status_code, 410, path)

    def test_puzzle_pages_live_at_site_root(self):
        self.assertPermanentRedirectsTo('/old/meta100-1?key=AB', '/meta100-1?key=AB')
        self.assertEqual(self.client.get('/meta100-1?key=AB').json(), self.client.get('/meta100-1/?key=AB').json())
        self.assertIn('error', self.client.get('/meta100-2').json())
        self.assertEqual(self.client.get('/cw_hint').status_code, 503)
        response = self.client.get('/easter_egg_2021/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'easter_egg_rebus.png')
