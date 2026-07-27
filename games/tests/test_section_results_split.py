from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase

from games.ladder_daily import LADDER_GAME_ID
from games.models import CheckerType, Game, HTMLPage, Project
from games.results_snapshot import freeze_game_results, results_attempts_scope_game
from games.views.new_ui import new_section_results_page


def _ensure_min_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    CheckerType.objects.get_or_create(pk='replacements_lines')


class SectionResultsDisabledTests(TestCase):
    """Sections (ladder, replacements, …) no longer have a results table."""

    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def setUp(self):
        self.factory = RequestFactory()

    def _create_section_game(self, game_id='sec_res'):
        return Game.objects.create(
            id=game_id,
            name='Section',
            author='a',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )

    def test_section_results_page_is_404(self):
        self._create_section_game()
        request = self.factory.get('/section/sec_res/results/')
        request.user = AnonymousUser()
        request.session = {}
        with self.assertRaises(Http404):
            new_section_results_page(request, 'sec_res')

    def test_freeze_skips_sections(self):
        game = self._create_section_game('sec_freeze')
        obj, did = freeze_game_results(game, mode='general', overwrite=True)
        self.assertIsNone(obj)
        self.assertFalse(did)

    def test_results_attempts_scope_ladder_vs_section(self):
        section = self._create_section_game('sec_scope')
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        self.assertIsNone(results_attempts_scope_game(section, 'general'))
        self.assertIs(results_attempts_scope_game(ladder, 'general'), ladder)
        self.assertIs(results_attempts_scope_game(section, 'tournament'), section)
