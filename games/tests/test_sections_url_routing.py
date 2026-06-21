from django.test import Client, TestCase

from games.models import Game, Project
from games.views.new_ui import _project_urls_context


class SectionsUrlRoutingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        Project.objects.get_or_create(pk='sections', defaults={})
        Game.objects.get_or_create(
            id='replacements',
            defaults={
                'name': 'Replacements',
                'author': 'a',
                'author_extra': '',
                'project_id': 'sections',
                'is_ready': True,
            },
        )

    def setUp(self):
        self.client = Client()

    def test_sections_project_does_not_get_url_prefix(self):
        ctx = _project_urls_context('sections')
        self.assertEqual(ctx['ui_project_base'], '')
        self.assertEqual(ctx['ui_project_games_url'], '/games/')
        self.assertEqual(ctx['ui_project_home_url'], '/')

    def test_legacy_sections_games_redirects_to_games(self):
        resp = self.client.get('/sections/games/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/games/')
