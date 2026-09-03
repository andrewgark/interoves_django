import threading
from contextlib import contextmanager
from unittest import skipUnless
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from games.analytics import register_completed_game, register_started_game
from games.analytics_persistence import (
    ANALYTICS_UNIQUE_SPECS,
    create_or_reread_analytics_row,
)
from games.account_merge import merge_accounts
from games.anon_migrate import migrate_anon_started_games
from games.models import (
    AccountMerge,
    CheckerType,
    Game,
    HTMLPage,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Project,
    Task,
    TaskGroup,
)


@skipUnless(connection.vendor == 'mysql', 'requires a dedicated MySQL test database')
class ProductAnalyticsMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_test_indexes()

    @classmethod
    def _ensure_test_indexes(cls):
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            for spec in ANALYTICS_UNIQUE_SPECS:
                model = PlayerStartedGame
                if spec.model_name == 'PlayerCompletedGame':
                    model = PlayerCompletedGame
                elif spec.model_name == 'PlayerAnalyticsState':
                    model = PlayerAnalyticsState
                constraints = connection.introspection.get_constraints(
                    cursor, model._meta.db_table,
                )
                if spec.index_name in constraints:
                    continue
                cursor.execute(
                    'CREATE UNIQUE INDEX {} ON {} ({}) '
                    'ALGORITHM=INPLACE LOCK=NONE'.format(
                        quote(spec.index_name),
                        quote(model._meta.db_table),
                        ', '.join(quote(column) for column in spec.columns),
                    )
                )

    def setUp(self):
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        project, _ = Project.objects.get_or_create(pk='main')
        checker, _ = CheckerType.objects.get_or_create(
            pk='equals_with_possible_spaces',
        )
        self.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'MySQL analytics race',
                'author': 'test',
                'project': project,
                'is_ready': True,
            },
        )
        self.user = User.objects.create_user(username='mysql-analytics-race-user')
        self.other_user = User.objects.create_user(username='mysql-analytics-race-other')
        self.tasks = []
        for number in range(1, 7):
            task_group = TaskGroup.objects.create(label='mysql-race-{}'.format(number))
            self.tasks.append(Task.objects.create(
                task_group=task_group,
                number='1',
                task_type='raddle',
                checker=checker,
                answer='TEST',
            ))

    @contextmanager
    def _synchronize_first_missing_reads(self, parties=2):
        from games import analytics_persistence

        original = analytics_persistence.read_exact_analytics_row
        barrier = threading.Barrier(parties, timeout=10)
        local = threading.local()

        def synchronized(*args, **kwargs):
            row = original(*args, **kwargs)
            if row is None and not getattr(local, 'waited', False):
                local.waited = True
                barrier.wait()
            return row

        with patch(
            'games.analytics_persistence.read_exact_analytics_row',
            side_effect=synchronized,
        ):
            yield

    def _parallel(self, *functions):
        results = [None] * len(functions)
        errors = []

        def run(position, function):
            close_old_connections()
            try:
                results[position] = function()
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=run, args=(position, function))
            for position, function in enumerate(functions)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        return results

    def test_two_parallel_user_starts_return_one_canonical_row(self):
        task = self.tasks[0]
        with self._synchronize_first_missing_reads():
            results = self._parallel(*(
                lambda: register_started_game(user=self.user, task=task, game=self.game)
                for _ in range(2)
            ))
        rows = PlayerStartedGame.objects.filter(user=self.user)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(results[0][0]['key'], results[1][0]['key'])

    def test_two_parallel_anon_starts_return_one_canonical_row(self):
        task = self.tasks[0]
        anon_key = 'mysql-race-anonymous-key'
        with self._synchronize_first_missing_reads():
            self._parallel(*(
                lambda: register_started_game(
                    anon_key=anon_key,
                    task=task,
                    game=self.game,
                )
                for _ in range(2)
            ))
        self.assertEqual(PlayerStartedGame.objects.filter(anon_key=anon_key).count(), 1)

    def test_two_parallel_completions_return_one_canonical_row(self):
        task = self.tasks[0]
        with self._synchronize_first_missing_reads():
            results = self._parallel(*(
                lambda: register_completed_game(user=self.user, task=task, game=self.game)
                for _ in range(2)
            ))
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)
        completion_keys = [
            next(goal['key'] for goal in result if goal['goal'] == 'game_complete')
            for result in results
        ]
        self.assertEqual(completion_keys[0], completion_keys[1])

    def test_distinct_actors_same_placement_do_not_conflict(self):
        task = self.tasks[0]
        self._parallel(
            lambda: register_started_game(user=self.user, task=task, game=self.game),
            lambda: register_started_game(user=self.other_user, task=task, game=self.game),
        )
        self.assertEqual(PlayerStartedGame.objects.count(), 2)

    def test_same_actor_distinct_placements_do_not_conflict(self):
        self._parallel(
            lambda: register_started_game(
                user=self.user, task=self.tasks[0], game=self.game,
            ),
            lambda: register_started_game(
                user=self.user, task=self.tasks[1], game=self.game,
            ),
        )
        self.assertEqual(PlayerStartedGame.objects.filter(user=self.user).count(), 2)

    def test_parallel_state_creation_returns_one_canonical_row(self):
        lookup = {'user': self.user, 'team': None, 'anon_key': None}
        with self._synchronize_first_missing_reads():
            results = self._parallel(*(
                lambda: create_or_reread_analytics_row(
                    PlayerAnalyticsState,
                    lookup=lookup,
                )
                for _ in range(2)
            ))
        self.assertEqual(PlayerAnalyticsState.objects.filter(user=self.user).count(), 1)
        self.assertEqual(results[0][0].pk, results[1][0].pk)

    def test_parallel_activation_keeps_one_consistent_state(self):
        for task in self.tasks[:2]:
            register_completed_game(user=self.user, task=task, game=self.game)
        results = self._parallel(
            lambda: register_completed_game(
                user=self.user, task=self.tasks[2], game=self.game,
            ),
            lambda: register_completed_game(
                user=self.user, task=self.tasks[3], game=self.game,
            ),
        )
        state = PlayerAnalyticsState.objects.get(user=self.user)
        self.assertIsNotNone(state.activated_at)
        self.assertFalse(state.activation_is_backfilled)
        activation_keys = {
            goal['key']
            for result in results
            for goal in result
            if goal['goal'] == 'activated_player'
        }
        self.assertEqual(activation_keys, {'activated_player:{}'.format(state.pk)})

    def test_parallel_claim_with_existing_target_is_idempotent(self):
        task = self.tasks[0]
        instance_id = '{}:{}'.format(self.game.pk, task.task_group_id)
        target = PlayerStartedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=task.task_group,
            game_kind='target',
            game_instance_id=instance_id,
            public_game_id='target',
        )
        source = PlayerStartedGame.objects.create(
            anon_key='mysql-parallel-claim',
            game=self.game,
            task_group=task.task_group,
            game_kind='source',
            game_instance_id=instance_id,
            public_game_id='source',
        )
        PlayerStartedGame.objects.filter(pk=target.pk).update(
            started_at=source.started_at,
        )

        results = self._parallel(
            lambda: migrate_anon_started_games(self.user, source.anon_key),
            lambda: migrate_anon_started_games(self.user, source.anon_key),
        )

        self.assertEqual(sum(results), 1)
        self.assertEqual(PlayerStartedGame.objects.filter(user=self.user).count(), 1)
        self.assertFalse(PlayerStartedGame.objects.filter(anon_key=source.anon_key).exists())

    def test_parallel_account_merge_returns_same_completed_merge(self):
        SocialAccount.objects.create(
            user=self.other_user,
            provider='vk',
            uid='mysql-race-source-vk',
            extra_data={},
        )

        results = self._parallel(
            lambda: merge_accounts(
                target_user=self.user,
                source_user=self.other_user,
                provider='vk',
                provider_uid='mysql-race-source-vk',
            ),
            lambda: merge_accounts(
                target_user=self.user,
                source_user=self.other_user,
                provider='vk',
                provider_uid='mysql-race-source-vk',
            ),
        )

        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(AccountMerge.objects.count(), 1)

    def test_code_remains_compatible_before_user_start_index(self):
        spec = next(
            item for item in ANALYTICS_UNIQUE_SPECS
            if item.index_name == 'uniq_started_game_user_instance'
        )
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX {} ON {}'.format(
                quote(spec.index_name),
                quote(PlayerStartedGame._meta.db_table),
            ))
        try:
            task = self.tasks[0]
            with self._synchronize_first_missing_reads():
                self._parallel(*(
                    lambda: register_started_game(
                        user=self.user,
                        task=task,
                        game=self.game,
                    )
                    for _ in range(2)
                ))
            self.assertEqual(PlayerStartedGame.objects.filter(user=self.user).count(), 2)
        finally:
            rows = list(PlayerStartedGame.objects.filter(
                user=self.user,
            ).order_by('pk'))
            if rows:
                PlayerStartedGame.objects.filter(
                    user=self.user,
                ).exclude(pk=rows[0].pk).delete()
            with connection.cursor() as cursor:
                cursor.execute(
                    'CREATE UNIQUE INDEX {} ON {} ({}) '
                    'ALGORITHM=INPLACE LOCK=NONE'.format(
                        quote(spec.index_name),
                        quote(PlayerStartedGame._meta.db_table),
                        ', '.join(quote(column) for column in spec.columns),
                    )
                )
