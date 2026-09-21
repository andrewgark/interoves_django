import json
from decimal import Decimal

from django.test import TestCase
from django.db import transaction
from django.core.management import call_command
from io import StringIO
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User

from games.daily_result_projection import (
    _canonical_group_results,
    refresh_daily_result_projection,
    schedule_actor_projection,
)
from games.models import (
    Attempt, DailyResultProjection, DailyResultProjectionState, Game, GameTaskGroup, HTMLPage, Project,
    ChainTaskState, Hint, HintAttempt, Profile, ReplaySlot, Task, TaskGroup, HiddenAnonKey,
)
from games.word_salad import dump_state, score_for_state


class DailyResultProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={'name': 'Sections'})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

    def setUp(self):
        self.game = Game.objects.create(
            id='projection_test', name='Projection test', author='test', author_extra='',
            project_id='sections', is_ready=True,
        )
        self.group = TaskGroup.objects.create(label='projection group')
        GameTaskGroup.objects.create(game=self.game, task_group=self.group, number='1', name='Release 1')

    def test_salad_projection_uses_canonical_state_calculator(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='word_salad', points=1,
            checker_data=json.dumps({'grid': list('abcdefghijklmnop'), 'words': ['abc']}), text='Salad',
        )
        state = dump_state({'solved_indices': [0, 1, 1, 20], 'hint_counts': {'0': 1, '3': 2}})
        attempt = Attempt.manager.create(
            task=task, game=self.game, anon_key='salad-projection-actor',
            text='solve', status='Partial', points=0, state=state,
        )
        expected = score_for_state(attempt.state)
        results = _canonical_group_results(self.game, self.group)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[('anon', 'salad-projection-actor')]['score'], expected)
        refresh_daily_result_projection(self.game, self.group, results=results)
        projection = DailyResultProjection.objects.get(game=self.game, task_group=self.group)
        self.assertEqual(projection.score, expected)

    def test_ordinary_projection_uses_best_attempt_and_hint_penalty(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='default', points=10,
            checker_data='answer', text='Question',
        )
        Attempt.manager.create(task=task, game=self.game, anon_key='hinted', text='a', status='Wrong', points=2)
        Attempt.manager.create(task=task, game=self.game, anon_key='hinted', text='answer', status='Ok', points=9)
        hint = Hint.objects.create(task=task, number='1', desc='hint', points_penalty=Decimal('2'))
        HintAttempt.objects.create(hint=hint, anon_key='hinted', is_real_request=True)
        result = _canonical_group_results(self.game, self.group)
        self.assertEqual(result[('anon', 'hinted')]['score'], Decimal('7'))
        refresh_daily_result_projection(self.game, self.group, results=result)
        self.assertEqual(DailyResultProjection.objects.get().score, Decimal('7'))

    def test_alphabetty_projection_uses_canonical_chain_hint_penalty(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='alphabetty', points=10,
            checker_data='secret', text='Alphabetty',
        )
        Attempt.manager.create(task=task, game=self.game, anon_key='alphabetty-actor', text='secret', status='Ok', points=10)
        ChainTaskState.objects.create(
            task=task, game=self.game, game_mode='general', anon_key='alphabetty-actor',
            state=json.dumps({'hints_taken': 2}),
        )
        results = _canonical_group_results(self.game, self.group)
        self.assertEqual(results[('anon', 'alphabetty-actor')]['score'], Decimal('8'))

    def test_replay_is_ignored_and_refresh_is_idempotent(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='default', points=10,
            checker_data='answer', text='Question',
        )
        Attempt.manager.create(
            task=task, game=self.game, anon_key='replay-actor', text='answer',
            status='Ok', points=7,
        )
        slot = ReplaySlot.objects.create(
            game=self.game, task_group=self.group, anon_key='replay-actor', actor_key='a:replay-actor',
        )
        Attempt.manager.create(
            task=task, game=self.game, anon_key='replay-actor', replay_slot=slot,
            text='replay', status='Ok', points=99,
        )
        first = _canonical_group_results(self.game, self.group)
        self.assertEqual(first[('anon', 'replay-actor')]['score'], Decimal('7'))
        refresh_daily_result_projection(self.game, self.group, results=first)
        refresh_daily_result_projection(self.game, self.group)
        self.assertEqual(DailyResultProjection.objects.filter(game=self.game, task_group=self.group).count(), 1)
        self.assertEqual(DailyResultProjection.objects.get().score, Decimal('7'))

    def test_actor_projection_hook_runs_after_commit_and_is_discarded_on_rollback(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='default', points=10,
            checker_data='answer', text='Question',
        )
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                Attempt.manager.create(
                    task=task, game=self.game, anon_key='committed-hook',
                    text='answer', status='Ok', points=7,
                )
                schedule_actor_projection(
                    self.game, self.group, anon_key='committed-hook',
                )
        self.assertEqual(DailyResultProjection.objects.get().score, Decimal('7'))

        DailyResultProjection.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    Attempt.manager.create(
                        task=task, game=self.game, anon_key='rolled-back-hook',
                        text='answer', status='Ok', points=9,
                    )
                    schedule_actor_projection(
                        self.game, self.group, anon_key='rolled-back-hook',
                    )
                    raise RuntimeError('rollback source write')
            except RuntimeError:
                pass
        self.assertFalse(DailyResultProjection.objects.exists())

    def test_rebuild_command_dry_run_then_idempotent_apply(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='default', points=1,
            checker_data='answer', text='Question',
        )
        Attempt.manager.create(
            task=task, game=self.game, anon_key='command-actor', text='answer',
            status='Ok', points=1,
        )
        call_command('rebuild_daily_result_summaries', game=self.game.pk, stdout=StringIO())
        self.assertFalse(DailyResultProjection.objects.exists())
        call_command('rebuild_daily_result_summaries', game=self.game.pk, apply=True, stdout=StringIO())
        first_count = DailyResultProjection.objects.count()
        self.assertEqual(first_count, 1)
        call_command('rebuild_daily_result_summaries', game=self.game.pk, apply=True, stdout=StringIO())
        self.assertEqual(DailyResultProjection.objects.count(), first_count)

    def test_reconciliation_reports_missing_extra_wrong_score_and_stale_version_read_only(self):
        task = Task.objects.create(
            task_group=self.group, number='1', task_type='default', points=10,
            checker_data='answer', text='Question',
        )
        Attempt.manager.create(task=task, game=self.game, anon_key='reconcile-a', text='answer', status='Ok', points=7)
        Attempt.manager.create(task=task, game=self.game, anon_key='reconcile-b', text='answer', status='Ok', points=8)
        DailyResultProjection.objects.create(
            game=self.game, task_group=self.group, actor_type='anon',
            actor_key='reconcile-a', anon_key='reconcile-a', score=6,
        )
        DailyResultProjection.objects.create(
            game=self.game, task_group=self.group, actor_type='anon',
            actor_key='reconcile-orphan', anon_key='reconcile-orphan', score=2,
        )
        state = DailyResultProjectionState.objects.create(
            game=self.game, task_group=self.group, adapter_version=0,
        )
        output = StringIO()
        call_command('rebuild_daily_result_summaries', game=self.game.pk, reconcile=True, stdout=output)
        self.assertIn('missing=1 extra=1 score_mismatch=1 stale_version=True', output.getvalue())
        self.assertEqual(DailyResultProjection.objects.count(), 2)
        state.refresh_from_db()
        self.assertEqual(state.adapter_version, 0)

    def test_sql_eligibility_excludes_author_cell_but_keeps_actor_other_release(self):
        from games.aggregate_leaderboard import build_aggregate_page

        second = TaskGroup.objects.create(label='projection second group')
        GameTaskGroup.objects.create(game=self.game, task_group=second, number='2', name='Release 2')
        task_a = Task.objects.create(task_group=self.group, number='1', task_type='default', points=10, checker_data='x', text='x')
        task_b = Task.objects.create(task_group=second, number='1', task_type='default', points=10, checker_data='x', text='x')
        user = User.objects.create_user(username='projection-author')
        profile = Profile.objects.create(user=user, first_name='Author', last_name='Player')
        hidden_user = User.objects.create_user(username='projection-hidden')
        Profile.objects.create(user=hidden_user, first_name='Hidden', last_name='Player', is_hidden=True)
        self.group.authors.add(profile)
        Attempt.manager.create(task=task_a, game=self.game, user=user, text='x', status='Ok', points=8)
        Attempt.manager.create(task=task_b, game=self.game, user=user, text='x', status='Ok', points=7)
        Attempt.manager.create(task=task_b, game=self.game, user=hidden_user, text='x', status='Ok', points=10)
        Attempt.manager.create(task=task_b, game=self.game, anon_key='prepub-actor', text='x', status='Ok', points=9)
        refresh_daily_result_projection(self.game, self.group)
        refresh_daily_result_projection(self.game, second)
        DailyResultProjection.objects.filter(
            game=self.game, task_group=second, anon_key='prepub-actor',
        ).update(is_prepublication=True)
        request = RequestFactory().get('/projection_test/results/')
        request.user = AnonymousUser()
        request.session = {}
        result = build_aggregate_page(request, self.game)
        self.assertEqual(len(result['aggregate_rows']), 1)
        row = result['aggregate_rows'][0]
        self.assertEqual(row['score'], Decimal('7'))
        self.assertEqual(row['played'], 1)
        self.assertEqual(set(row['cells']), {GameTaskGroup.objects.get(game=self.game, task_group=second).pk})


class ProjectionAggregatePerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={'name': 'Sections'})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

    def test_thirty_release_120_actor_pages_use_page_sized_sql_results(self):
        from games.aggregate_leaderboard import build_aggregate_page

        game = Game.objects.create(
            id='projection_perf_test', name='Projection perf', author='test', author_extra='',
            project_id='sections', is_ready=True,
        )
        groups = [TaskGroup(label='perf-{}'.format(i)) for i in range(1, 31)]
        TaskGroup.objects.bulk_create(groups)
        links = [GameTaskGroup(game=game, task_group=group, number=str(i), name='Release {}'.format(i)) for i, group in enumerate(groups, 1)]
        GameTaskGroup.objects.bulk_create(links)
        tasks = [Task(task_group=group, number='1', task_type='default', points=1, checker_data='x', text='Question') for group in groups]
        Task.objects.bulk_create(tasks)
        actors = ['perf-anon-{:03}'.format(i) for i in range(120)]
        hidden = HiddenAnonKey.objects.create(anon_key=actors[-1])
        attempts = []
        for actor_n, anon_key in enumerate(actors[:-1]):
            for release_n, task in enumerate(tasks):
                if (actor_n + release_n) % 17 == 0:
                    continue
                attempts.append(Attempt(
                    task=task, game=game, anon_key=anon_key, text='x', status='Ok', points=1,
                ))
        Attempt.manager.bulk_create(attempts, batch_size=500)
        for group in groups:
            refresh_daily_result_projection(game, group)

        factory = RequestFactory()
        def page(path):
            request = factory.get(path)
            request.user = AnonymousUser()
            request.session = {}
            return request

        with CaptureQueriesContext(connection) as ten_release_queries:
            ten = build_aggregate_page(page('/projection_perf_test/results/?limit=10'), game)
        with CaptureQueriesContext(connection) as first_queries:
            first = build_aggregate_page(page('/projection_perf_test/results/?limit=30'), game)
        with CaptureQueriesContext(connection) as second_queries:
            second = build_aggregate_page(page('/projection_perf_test/results/?limit=30&page=2'), game)
        self.assertEqual(len(first['aggregate_columns']), 30)
        self.assertEqual(len(ten['aggregate_columns']), 10)
        self.assertEqual(first['aggregate_page'].paginator.count, 119)
        self.assertEqual(len(first['aggregate_rows']), 50)
        self.assertEqual(len(second['aggregate_rows']), 50)
        oracle = {}
        for item in DailyResultProjection.objects.filter(game=game).values('actor_key', 'task_group_id', 'score'):
            data = oracle.setdefault(item['actor_key'], {'score': Decimal('0'), 'played': 0, 'cells': {}})
            data['score'] += item['score']
            data['played'] += 1
            data['cells'][item['task_group_id']] = item['score']
        ordered = sorted(oracle, key=lambda actor_key: (-oracle[actor_key]['score'], actor_key))
        expected_rank = {}
        prior = object()
        rank = 0
        for index, actor_key in enumerate(ordered, 1):
            if oracle[actor_key]['score'] != prior:
                rank = index
                prior = oracle[actor_key]['score']
            expected_rank[actor_key] = rank
        for index, row in enumerate(first['aggregate_rows'] + second['aggregate_rows']):
            anon_key = row['actor'].anon_key
            self.assertEqual(row['score'], oracle[anon_key]['score'])
            self.assertEqual(row['played'], oracle[anon_key]['played'])
            self.assertEqual(row['place'], expected_rank[anon_key], '{} score={} place={} expected={}'.format(anon_key, row['score'], row['place'], expected_rank[anon_key]))
            self.assertEqual(len(row['cells']), len(oracle[anon_key]['cells']))
        self.assertLessEqual(sum(len(row['cells']) for row in first['aggregate_rows']), 50 * 30)
        self.assertEqual(len(first_queries), 10)
        self.assertEqual(len(second_queries), 10)
        self.assertEqual(len(ten_release_queries), 10)
        self.assertEqual(len(first_queries), len(second_queries))
        from games.aggregate_leaderboard import _build_legacy_aggregate_page
        legacy_first = _build_legacy_aggregate_page(page('/projection_perf_test/results/?limit=30'), game)
        legacy_second = _build_legacy_aggregate_page(page('/projection_perf_test/results/?limit=30&page=2'), game)
        new_rows = first['aggregate_rows'] + second['aggregate_rows']
        legacy_rows = list(legacy_first['aggregate_rows']) + list(legacy_second['aggregate_rows'])
        def normalized(row):
            actor_key = row['actor'].anon_key
            return (
                actor_key, row['score'], row['played'], row['place'],
                {link_id: value for link_id, value in row['cells'].items()},
            )
        self.assertEqual([normalized(row) for row in new_rows], [normalized(row) for row in legacy_rows])
        hidden.delete()
