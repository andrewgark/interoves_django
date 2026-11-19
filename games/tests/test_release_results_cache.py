from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from games.models import DailyResultProjectionState, Game, GameTaskGroup, Project, TaskGroup
from games.site.pages.results import compute


class ReleaseResultsCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        project, _ = Project.objects.get_or_create(id='sections')
        cls.game, _ = Game.objects.get_or_create(
            id='salad',
            defaults={'name': 'salad', 'author': 't', 'project': project,
                      'rules': None, 'tournament_rules': None},
        )
        cls.tg = TaskGroup.objects.create(label='release cache')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number='7', name='7')
        cls.state = DailyResultProjectionState.objects.create(
            game=cls.game, task_group=cls.tg, source_revision=3,
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _compute(self):
        return compute._new_results_compute(self.game, 'general', task_group_number='7')

    def test_release_table_is_reused_until_its_source_revision_changes(self):
        with patch.object(compute, '_new_results_compute_uncached', side_effect=[{'n': 1}, {'n': 2}]) as build:
            self.assertEqual(self._compute(), {'n': 1})
            self.assertEqual(self._compute(), {'n': 1})
            DailyResultProjectionState.objects.filter(pk=self.state.pk).update(source_revision=4)
            self.assertEqual(self._compute(), {'n': 2})
        self.assertEqual(build.call_count, 2)

    def test_visibility_change_rebuilds_the_release_table(self):
        with patch.object(compute, '_new_results_compute_uncached', side_effect=[{'n': 1}, {'n': 2}]):
            self.assertEqual(self._compute(), {'n': 1})
            compute.bump_results_visibility_generation()
            self.assertEqual(self._compute(), {'n': 2})
