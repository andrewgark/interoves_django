from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from games.models import (
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyAggregateSnapshotVersion,
    Game,
    HTMLPage,
    Project,
    Team,
)
from games.results.standings.aggregate_leaderboard import _build_projection_aggregate_page
from games.results.snapshots.aggregate_snapshot_read import read_snapshot_page
from games.results.snapshots.aggregate_snapshots import SnapshotVersionExpired


class AggregateSnapshotReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.game = Game.objects.create(
            id='snapshot_read_test', name='Snapshot read', author='test',
            author_extra='', project_id='sections', is_ready=True,
        )
        snapshot = DailyAggregateSnapshot.objects.create(
            game=cls.game, month=date(2026, 9, 1),
        )
        cls.version = DailyAggregateSnapshotVersion.objects.create(
            snapshot=snapshot, columns=[], source_versions={},
        )
        for index in range(3):
            team = Team.objects.create(
                name='snapshot-team-{}'.format(index),
                visible_name='Snapshot team {}'.format(index),
            )
            DailyAggregateSnapshotRow.objects.create(
                version=cls.version,
                actor_type='team', actor_key=team.pk, team=team,
                score=Decimal(10 - index), attempts_count=index + 1,
                played_count=1, cells={},
            )

    def test_paged_reads_keep_view_token_when_visibility_is_unchanged(self):
        first_rows, first_total, first_page, _, first_view = read_snapshot_page(
            self.version, 1, {'team'}, 'time', page_size=2,
        )
        second_rows, second_total, second_page, _, second_view = read_snapshot_page(
            self.version, 2, {'team'}, 'time', page_size=2,
        )

        self.assertEqual((first_total, second_total), (3, 3))
        self.assertEqual((first_page, second_page), (1, 2))
        self.assertEqual(first_view, second_view)
        self.assertTrue({row['actor_key'] for row in first_rows}.isdisjoint(
            row['actor_key'] for row in second_rows
        ))

    def test_empty_actor_filter_still_returns_view_token(self):
        rows, total, page, cells, view = read_snapshot_page(
            self.version, 1, set(), 'time',
        )

        self.assertEqual((rows, total, page, cells), ([], 0, 1, {}))
        self.assertEqual(len(view), 64)

    def test_progressive_page_request_sends_snapshot_view_token(self):
        script = (Path(__file__).resolve().parents[2] / 'static/js/results_progressive.js').read_text()
        load_next = script.split('async function loadNext()', 1)[1].split('catch (error)', 1)[0]

        self.assertIn("url.searchParams.set('snapshot_view', snapshotView);", load_next)

    def test_visibility_change_changes_view_token(self):
        _, _, _, _, original_view = read_snapshot_page(
            self.version, 1, {'team'}, 'time', page_size=2,
        )
        hidden_team = Team.objects.get(pk='snapshot-team-0')
        hidden_team.is_hidden = True
        hidden_team.save(update_fields=['is_hidden'])

        _, total, _, _, changed_view = read_snapshot_page(
            self.version, 1, {'team'}, 'time', page_size=2,
        )

        self.assertEqual(total, 2)
        self.assertNotEqual(original_view, changed_view)

    def _saved_page(self, query):
        request = RequestFactory().get('/snapshot_read_test/results/', query)
        request.user = AnonymousUser()
        request.session = {}
        return request, _build_projection_aggregate_page(
            request, self.game, [], aggregate_period='month',
            saved_monthly=True, snapshot_version=self.version,
        )

    def test_partial_page_accepts_matching_view_token(self):
        _, _, _, _, view = read_snapshot_page(self.version, 1, {'team'}, 'time')

        request, _ = self._saved_page({'actors': 'team', 'page': '1', 'partial': '1', 'snapshot_view': view})

        self.assertEqual(request._aggregate_snapshot_view, view)

    def test_partial_page_rejects_missing_or_stale_view_token(self):
        for token in (None, 'stale'):
            query = {'actors': 'team', 'page': '1', 'partial': '1'}
            if token is not None:
                query['snapshot_view'] = token
            with self.subTest(token=token), self.assertRaises(SnapshotVersionExpired):
                self._saved_page(query)

    def test_saved_page_with_every_actor_type_filtered_out(self):
        request, data = self._saved_page({'actors': ''})

        self.assertEqual(data['aggregate_page'].paginator.count, 0)
        self.assertEqual(len(request._aggregate_snapshot_view), 64)

    def test_results_view_turns_expired_snapshot_into_reload_response(self):
        with patch(
            'games.results.standings.aggregate_leaderboard.build_aggregate_page',
            side_effect=SnapshotVersionExpired(),
        ):
            response = self.client.get('/ladder/results/', {'partial': '1', 'page': '2'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response['X-Results-Reload'], '1')
