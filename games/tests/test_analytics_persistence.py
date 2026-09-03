from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from games.analytics import register_started_game
from games.analytics_persistence import (
    AnalyticsRowInvariantError,
    _mysql_duplicate_matches,
    analytics_unique_spec,
    create_or_reread_analytics_row,
    merge_started_analytics_rows,
    reassign_or_merge_analytics_row,
)
from games.anon_migrate import (
    migrate_anon_analytics_state,
    migrate_anon_completed_games,
    migrate_anon_started_games,
)
from games.models import (
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


class AnalyticsPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.project, _ = Project.objects.get_or_create(pk='main')
        cls.game = Game.objects.create(
            id='analytics-persistence',
            name='Analytics persistence',
            author='test',
            project=cls.project,
            is_ready=True,
        )
        cls.task_group = TaskGroup.objects.create(label='analytics persistence')
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            task_type='default',
            checker=checker,
            answer='TEST',
        )
        cls.user = User.objects.create_user(username='analytics-persistence-user')

    @property
    def instance_id(self):
        return '{}:{}'.format(self.game.pk, self.task_group.pk)

    def _start(self, **overrides):
        values = {
            'game': self.game,
            'task_group': self.task_group,
            'game_kind': 'start-kind',
            'game_instance_id': self.instance_id,
            'public_game_id': 'start-public',
        }
        values.update(overrides)
        return PlayerStartedGame.objects.create(**values)

    def _completion(self, **overrides):
        values = {
            'game': self.game,
            'task_group': self.task_group,
            'game_kind': 'completion-kind',
            'game_instance_id': self.instance_id,
            'public_game_id': 'completion-public',
            'result': PlayerCompletedGame.RESULT_COMPLETED,
        }
        values.update(overrides)
        return PlayerCompletedGame.objects.create(**values)

    def test_expected_duplicate_returns_exact_canonical_row(self):
        existing = self._start(user=self.user)
        lookup = {
            'user': self.user,
            'team': None,
            'anon_key': None,
            'game_instance_id': self.instance_id,
        }
        with patch(
            'games.analytics_persistence.read_exact_analytics_row',
            side_effect=[None, existing],
        ):
            row, created = create_or_reread_analytics_row(
                PlayerStartedGame,
                lookup=lookup,
                defaults={
                    'game': self.game,
                    'task_group': self.task_group,
                    'game_kind': 'start-kind',
                },
            )
        self.assertFalse(created)
        self.assertEqual(row.pk, existing.pk)

    def test_expected_duplicate_without_canonical_reraises_integrity_error(self):
        self._start(user=self.user)
        lookup = {
            'user': self.user,
            'team': None,
            'anon_key': None,
            'game_instance_id': self.instance_id,
        }
        with patch(
            'games.analytics_persistence.read_exact_analytics_row',
            side_effect=[None, None],
        ):
            with self.assertRaises(IntegrityError):
                create_or_reread_analytics_row(
                    PlayerStartedGame,
                    lookup=lookup,
                    defaults={
                        'game': self.game,
                        'task_group': self.task_group,
                        'game_kind': 'start-kind',
                    },
                )

    def test_multiple_canonical_rows_raise_invariant_error(self):
        self._start(user=self.user)
        lookup = {
            'user': self.user,
            'team': None,
            'anon_key': None,
            'game_instance_id': self.instance_id,
        }
        invariant = AnalyticsRowInvariantError('multiple canonical rows')
        with patch(
            'games.analytics_persistence.read_exact_analytics_row',
            side_effect=[None, invariant],
        ):
            with self.assertRaises(AnalyticsRowInvariantError):
                create_or_reread_analytics_row(
                    PlayerStartedGame,
                    lookup=lookup,
                    defaults={
                        'game': self.game,
                        'task_group': self.task_group,
                        'game_kind': 'start-kind',
                    },
                )

    def test_unrelated_integrity_error_is_not_hidden(self):
        lookup = {
            'user': self.user,
            'team': None,
            'anon_key': None,
            'game_instance_id': self.instance_id,
        }
        with self.assertRaises(IntegrityError):
            create_or_reread_analytics_row(
                PlayerStartedGame,
                lookup=lookup,
                defaults={
                    'game': None,
                    'task_group': self.task_group,
                    'game_kind': 'start-kind',
                },
            )

    def test_mysql_matcher_rejects_deadlocks_timeouts_and_other_keys(self):
        spec = analytics_unique_spec(PlayerStartedGame, {
            'user': self.user,
            'game_instance_id': self.instance_id,
        })
        expected = IntegrityError(
            1062,
            "Duplicate entry 'redacted' for key 'games_playerstartedgame.{}'".format(
                spec.index_name,
            ),
        )
        other = IntegrityError(1062, "Duplicate entry 'redacted' for key 'other_key'")
        deadlock = IntegrityError(1213, 'Deadlock found')
        timeout = IntegrityError(1205, 'Lock wait timeout exceeded')
        self.assertTrue(_mysql_duplicate_matches(expected, spec))
        self.assertFalse(_mysql_duplicate_matches(other, spec))
        self.assertFalse(_mysql_duplicate_matches(deadlock, spec))
        self.assertFalse(_mysql_duplicate_matches(timeout, spec))

    def test_start_merge_uses_complete_earlier_backfill_bundle(self):
        now = timezone.now()
        target = self._start(
            user=self.user,
            game_kind='target-kind',
            public_game_id='target-public',
            is_backfilled=False,
            instrumentation_version=2,
        )
        source = self._start(
            anon_key='anon-start-bundle',
            game_kind='source-kind',
            public_game_id='source-public',
            is_backfilled=True,
            instrumentation_version=None,
            metrika_acked_at=now - timedelta(minutes=8),
        )
        PlayerStartedGame.objects.filter(pk=target.pk).update(
            started_at=now - timedelta(minutes=5),
        )
        PlayerStartedGame.objects.filter(pk=source.pk).update(
            started_at=now - timedelta(minutes=10),
        )

        self.assertEqual(migrate_anon_started_games(self.user, source.anon_key), 1)

        canonical = PlayerStartedGame.objects.get(pk=target.pk)
        self.assertEqual(canonical.started_at, now - timedelta(minutes=10))
        self.assertEqual(canonical.game_kind, 'source-kind')
        self.assertEqual(canonical.public_game_id, 'source-public')
        self.assertTrue(canonical.is_backfilled)
        self.assertIsNone(canonical.instrumentation_version)
        self.assertEqual(canonical.metrika_acked_at, now - timedelta(minutes=8))
        self.assertFalse(PlayerStartedGame.objects.filter(pk=source.pk).exists())

    def test_start_merge_does_not_transfer_ack_for_different_final_payload(self):
        now = timezone.now()
        target = self._start(
            user=self.user,
            game_kind=self.game.pk,
            public_game_id=str(self.task_group.pk),
        )
        source = self._start(
            anon_key='anon-start-ack',
            game_kind='source-kind',
            public_game_id='source-public',
            metrika_acked_at=now,
        )
        PlayerStartedGame.objects.filter(pk=target.pk).update(
            started_at=now - timedelta(minutes=10),
        )
        PlayerStartedGame.objects.filter(pk=source.pk).update(
            started_at=now - timedelta(minutes=5),
        )

        migrate_anon_started_games(self.user, source.anon_key)

        canonical = PlayerStartedGame.objects.get(pk=target.pk)
        self.assertEqual(canonical.game_kind, self.game.pk)
        self.assertIsNone(canonical.metrika_acked_at)
        goals = register_started_game(
            user=self.user,
            task=self.task,
            game=self.game,
        )
        self.assertEqual([goal['goal'] for goal in goals], ['game_start'])

    def test_start_merge_can_preserve_semantically_identical_source_ack(self):
        now = timezone.now()
        target = self._start(user=self.user, metrika_acked_at=None)
        source = self._start(
            anon_key='anon-start-equal-ack',
            metrika_acked_at=now,
        )
        PlayerStartedGame.objects.filter(pk=target.pk).update(
            started_at=now - timedelta(minutes=10),
        )
        PlayerStartedGame.objects.filter(pk=source.pk).update(
            started_at=now - timedelta(minutes=5),
        )

        migrate_anon_started_games(self.user, source.anon_key)

        self.assertEqual(
            PlayerStartedGame.objects.get(pk=target.pk).metrika_acked_at,
            now,
        )

    def test_completion_merge_uses_donor_result_without_ranking(self):
        now = timezone.now()
        target = self._completion(
            user=self.user,
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=False,
            instrumentation_version=2,
        )
        source = self._completion(
            anon_key='anon-completion-bundle',
            result=PlayerCompletedGame.RESULT_FAILED,
            is_backfilled=True,
            instrumentation_version=None,
        )
        PlayerCompletedGame.objects.filter(pk=target.pk).update(
            completed_at=now - timedelta(minutes=5),
        )
        PlayerCompletedGame.objects.filter(pk=source.pk).update(
            completed_at=now - timedelta(minutes=10),
        )

        migrate_anon_completed_games(self.user, source.anon_key)

        canonical = PlayerCompletedGame.objects.get(pk=target.pk)
        self.assertEqual(canonical.result, PlayerCompletedGame.RESULT_FAILED)
        self.assertEqual(canonical.completed_at, now - timedelta(minutes=10))
        self.assertTrue(canonical.is_backfilled)
        self.assertIsNone(canonical.instrumentation_version)

    def test_state_merge_keeps_signup_and_activation_bundles_separate(self):
        now = timezone.now()
        target = PlayerAnalyticsState.objects.create(
            user=self.user,
            signup_at=now - timedelta(days=10),
            signup_method='email',
            signup_goal_acked_at=now - timedelta(days=9),
            activated_at=now - timedelta(days=2),
            activation_is_backfilled=False,
            activation_goal_acked_at=now - timedelta(days=1),
        )
        source = PlayerAnalyticsState.objects.create(
            anon_key='anon-state-bundles',
            signup_at=now - timedelta(days=5),
            signup_method='vk',
            signup_goal_acked_at=now - timedelta(days=4),
            activated_at=now - timedelta(days=8),
            activation_is_backfilled=True,
            activation_goal_acked_at=now - timedelta(days=7),
        )

        migrate_anon_analytics_state(self.user, source.anon_key)

        canonical = PlayerAnalyticsState.objects.get(pk=target.pk)
        self.assertEqual(canonical.signup_at, now - timedelta(days=10))
        self.assertEqual(canonical.signup_method, 'email')
        self.assertEqual(canonical.signup_goal_acked_at, now - timedelta(days=9))
        self.assertEqual(canonical.activated_at, now - timedelta(days=8))
        self.assertTrue(canonical.activation_is_backfilled)
        self.assertIsNone(canonical.activation_goal_acked_at)

    def test_equal_timestamp_prefers_target_bundle(self):
        now = timezone.now()
        target = self._completion(
            user=self.user,
            result=PlayerCompletedGame.RESULT_COMPLETED,
        )
        source = self._completion(
            anon_key='anon-equal-timestamp',
            result=PlayerCompletedGame.RESULT_FAILED,
        )
        PlayerCompletedGame.objects.filter(pk__in=(target.pk, source.pk)).update(
            completed_at=now,
        )

        migrate_anon_completed_games(self.user, source.anon_key)

        self.assertEqual(
            PlayerCompletedGame.objects.get(pk=target.pk).result,
            PlayerCompletedGame.RESULT_COMPLETED,
        )

    def test_reassignment_rereads_target_that_won_race(self):
        target = self._start(user=self.user)
        source = self._start(anon_key='anon-reassignment-race')
        target_lookup = {
            'user': self.user,
            'game_instance_id': self.instance_id,
        }
        with patch(
            'games.analytics_persistence.read_exact_analytics_row',
            side_effect=[None, target],
        ):
            canonical = reassign_or_merge_analytics_row(
                source,
                target_lookup=target_lookup,
                identity_values={'user': self.user, 'anon_key': None},
                identity_update_fields=['user', 'anon_key'],
                merge_rows=merge_started_analytics_rows,
            )

        self.assertEqual(canonical.pk, target.pk)
        self.assertFalse(PlayerStartedGame.objects.filter(pk=source.pk).exists())
