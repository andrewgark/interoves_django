from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from games.models import Game, HTMLPage, Project
from games.support.services.stats import collect_support_stats


class SupportStatsServiceTests(TestCase):
    def setUp(self):
        Project.objects.get_or_create(pk='main')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        Game.objects.create(id='stats_a', name='Stats A', author='a')
        Game.objects.create(id='stats_b', name='Stats B', author='a')

    @staticmethod
    def _digest_stats():
        now = timezone.now()
        return {
            'since': now - timezone.timedelta(hours=24),
            'until': now,
            'attempts_total': 3,
            'attempts_by_status': {'Ok': 2, 'Wrong': 1},
            'active_users': 2,
            'active_teams': 1,
            'active_anon': 0,
            'hint_total': 0,
            'hint_users': 0,
            'top_games_attempts': [
                {'game_id': 'stats_a', 'attempts': 2, 'users': 2, 'teams': 1},
                {'game_id': 'stats_b', 'attempts': 1, 'users': 1, 'teams': 1},
            ],
            'top_games_users': [
                {'game_id': 'stats_b', 'attempts': 1, 'users': 1},
                {'game_id': 'stats_a', 'attempts': 2, 'users': 2},
            ],
            'registrations': 0,
            'new_accounts': 0,
            'tickets_pending': 0,
            'tickets_accepted': 0,
            'tickets_revenue': 0,
            'tickets_revenue_amd': 0,
            'bugs_total': 0,
            'bugs_pending': 0,
            'corporate_orders': 0,
            'pending_bugs_now': 0,
            'pending_tickets_now': 0,
            'stuck_tickets_now': 0,
        }

    @patch('games.support.services.stats.collect_daily_digest_stats')
    def test_reuses_digest_top_rows_and_bulk_loads_game_labels(self, digest_mock):
        digest_mock.return_value = self._digest_stats()

        with self.assertNumQueries(1):
            stats = collect_support_stats(hours=24, top_limit=2)

        digest_mock.assert_called_once()
        self.assertEqual(digest_mock.call_args.kwargs['top_limit'], 2)
        self.assertEqual(
            [row.game_label for row in stats.top_games_attempts],
            ['Stats A', 'Stats B'],
        )
        self.assertEqual(
            [row.game_label for row in stats.top_games_users],
            ['Stats B', 'Stats A'],
        )
