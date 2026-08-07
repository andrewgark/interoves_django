"""Snapshot builder uses bulk attempt loading (parity with per-task loaders)."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.results_snapshot import build_results_snapshot_payload


def _ensure_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


def _row_identity(row):
    return (row['row_kind'], row.get('team_id'), row.get('user_id'), row.get('anon_key'))


def _normalize_payload_for_compare(payload):
    rows = []
    for row in payload['rows']:
        cells = []
        for cell in row['cells']:
            if cell is None:
                cells.append(None)
                continue
            cells.append({
                'best_status': cell.get('best_status'),
                'n_attempts': cell['n_attempts'],
                'result_points': cell['result_points'],
                'sum_hint_penalty': cell['sum_hint_penalty'],
                'hint_numbers': list(cell['hint_numbers']),
                'cls': cell['cls'],
            })
        rows.append({
            'id': _row_identity(row),
            'score': row['score'],
            'place': row['place'],
            'cells': cells,
        })
    rows.sort(key=lambda r: (r['id'][0], str(r['id'][1]), str(r['id'][2]), str(r['id'][3])))
    return {
        'task_ids': list(payload['task_ids']),
        'rows': rows,
    }


def _bulk_via_per_task(manager, task_ids, mode='general', game=None):
    """Old per-task fan-out, shaped like get_bulk_game_actor_rows."""
    result = {}
    for task_id in task_ids:
        task = Task.objects.get(pk=task_id)
        if mode == 'general':
            rows = Attempt.manager.get_general_results_task_actor_rows(task=task, game=game)
        else:
            rows = []
            for ai in Attempt.manager.get_task_attempts_infos(task=task, mode=mode, game=game):
                if not (ai.attempts or ai.hint_attempts):
                    continue
                team = None
                if ai.attempts:
                    team = ai.attempts[0].team
                elif ai.hint_attempts:
                    team = ai.hint_attempts[0].team
                if not team or team.is_hidden:
                    continue
                rows.append((team, ai))
        result[task_id] = rows
    return result


class ResultsSnapshotBulkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        cls.game = Game.objects.create(
            id='snap_bulk_game',
            name='g',
            author='a',
            author_extra='',
            is_ready=True,
        )
        cls.tg = TaskGroup.objects.create(label='tg_snap_bulk')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='tg')
        with patch('games.views.track.track_task_change'):
            cls.task1 = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=10,
                checker_data='a',
                text='t1',
            )
            cls.task2 = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='equals_with_possible_spaces',
                points=10,
                checker_data='b',
                text='t2',
            )

        cls.team = Team.objects.create(name='snap_bulk_team', visible_name='Team A')
        cls.user = User.objects.create_user(username='snap_bulk_user', password='x')
        now = timezone.now()
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(
                text='a',
                status='Ok',
                points=10,
                time=now,
                task=cls.task1,
                team=cls.team,
                game=cls.game,
            )
            Attempt.manager.create(
                text='b',
                status='Partial',
                points=5,
                time=now,
                task=cls.task2,
                team=cls.team,
                game=cls.game,
            )
            Attempt.manager.create(
                text='a',
                status='Ok',
                points=10,
                time=now,
                task=cls.task1,
                user=cls.user,
                team=None,
                game=cls.game,
            )
            Attempt.manager.create(
                text='wrong',
                status='Wrong',
                points=0,
                time=now,
                task=cls.task2,
                anon_key='snap-bulk-anon',
                team=None,
                user=None,
                game=cls.game,
            )

    def test_general_uses_bulk_once_not_per_task(self):
        with patch(
            'games.results_snapshot.Attempt.manager.get_bulk_game_actor_rows',
            wraps=Attempt.manager.get_bulk_game_actor_rows,
        ) as bulk_mock:
            with patch(
                'games.results_snapshot.Attempt.manager.get_general_results_task_actor_rows',
                wraps=Attempt.manager.get_general_results_task_actor_rows,
            ) as per_task_mock:
                payload = build_results_snapshot_payload(self.game, mode='general')

        # General mode uses SQL aggregate (not ORM bulk / per-task).
        self.assertEqual(bulk_mock.call_count, 0)
        self.assertEqual(per_task_mock.call_count, 0)
        self.assertEqual(payload['task_ids'], [self.task1.id, self.task2.id])
        self.assertEqual(len(payload['rows']), 3)

    def test_general_payload_matches_per_task_oracle(self):
        from games.results_sql_aggregate import get_sql_aggregated_game_actor_rows

        bulk_payload = build_results_snapshot_payload(self.game, mode='general')
        with patch(
            'games.results_snapshot.Attempt.manager.get_bulk_game_actor_rows',
            side_effect=lambda task_ids, mode='general', game=None: _bulk_via_per_task(
                Attempt.manager, task_ids, mode=mode, game=game,
            ),
        ):
            with patch(
                'games.results_sql_aggregate.tasks_need_orm_results_aggregate',
                return_value=True,
            ):
                legacy_payload = build_results_snapshot_payload(self.game, mode='general')
        self.assertEqual(
            _normalize_payload_for_compare(bulk_payload),
            _normalize_payload_for_compare(legacy_payload),
        )
        # Also: SQL loader matches ORM bulk on the same fixture.
        task_ids = [self.task1.id, self.task2.id]
        orm = Attempt.manager.get_bulk_game_actor_rows(task_ids, mode='general', game=self.game)
        sql = get_sql_aggregated_game_actor_rows(task_ids, game=self.game)
        self.assertEqual(len(orm), len(sql))
        self.assertEqual(
            {(t, len(orm[t])) for t in task_ids},
            {(t, len(sql[t])) for t in task_ids},
        )

    def test_tournament_excludes_personal_rows(self):
        """Tournament snapshots stay team-only even if bulk returns personal actors."""
        from games.models import AttemptsInfo, PersonalResultsParticipant

        team_ai = AttemptsInfo(
            Attempt.manager.filter(team=self.team, task=self.task1).first(),
            list(Attempt.manager.filter(team=self.team, task=self.task1)),
            [],
        )
        personal_ai = AttemptsInfo(
            Attempt.manager.filter(user=self.user, task=self.task1).first(),
            list(Attempt.manager.filter(user=self.user, task=self.task1)),
            [],
        )
        fake_bulk = {
            self.task1.id: [
                (self.team, team_ai),
                (PersonalResultsParticipant(user=self.user), personal_ai),
            ],
            self.task2.id: [],
        }
        with patch(
            'games.results_snapshot.Attempt.manager.get_bulk_game_actor_rows',
            return_value=fake_bulk,
        ):
            payload = build_results_snapshot_payload(self.game, mode='tournament')
        kinds = {row['row_kind'] for row in payload['rows']}
        self.assertEqual(kinds, {'team'})
        self.assertEqual(payload['rows'][0]['team_id'], self.team.pk)
        self.assertEqual(payload['rows'][0]['score'], 10)

    def test_tournament_uses_bulk_once(self):
        with patch(
            'games.results_snapshot.Attempt.manager.get_bulk_game_actor_rows',
            wraps=Attempt.manager.get_bulk_game_actor_rows,
        ) as bulk_mock:
            with patch(
                'games.results_snapshot.Attempt.manager.get_task_attempts_infos',
                wraps=Attempt.manager.get_task_attempts_infos,
            ) as infos_mock:
                build_results_snapshot_payload(self.game, mode='tournament')
        self.assertEqual(bulk_mock.call_count, 1)
        self.assertEqual(infos_mock.call_count, 0)
