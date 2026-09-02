from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from games.analytics import register_completed_game, register_started_game
from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    PlayerCompletedGame,
    PlayerStartedGame,
    Project,
    Task,
    TaskGroup,
)


class ProductAnalyticsInstrumentationVersionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.user = User.objects.create_user(username='instrumentation-user')
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Ladder',
                'author': 'test',
                'project': cls.project,
                'requires_ticket': False,
                'is_tournament': False,
                'is_ready': True,
            },
        )
        cls.task_group = TaskGroup.objects.create(label='instrumentation-tg')
        GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.task_group,
            number='1',
            name='One',
        )
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            task_type='raddle',
            checker_id='equals_with_possible_spaces',
            answer='TEST',
        )

    def test_fields_are_nullable_and_have_no_model_default(self):
        for model in (PlayerStartedGame, PlayerCompletedGame):
            field = model._meta.get_field('instrumentation_version')
            self.assertTrue(field.null)
            self.assertFalse(field.has_default())

    def test_new_write_paths_explicitly_store_version_two(self):
        register_started_game(user=self.user, task=self.task, game=self.game)
        register_completed_game(user=self.user, task=self.task, game=self.game)

        self.assertEqual(PlayerStartedGame.objects.get().instrumentation_version, 2)
        self.assertEqual(PlayerCompletedGame.objects.get().instrumentation_version, 2)

    def test_legacy_null_rows_remain_readable_and_are_not_upgraded_on_retry(self):
        instance_id = 'ladder:{}'.format(self.task_group.pk)
        start = PlayerStartedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
        )
        completion = PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
            result=PlayerCompletedGame.RESULT_SOLVED,
        )

        self.assertIsNone(start.instrumentation_version)
        self.assertIsNone(completion.instrumentation_version)
        register_started_game(user=self.user, task=self.task, game=self.game)
        register_completed_game(user=self.user, task=self.task, game=self.game)
        start.refresh_from_db()
        completion.refresh_from_db()
        self.assertIsNone(start.instrumentation_version)
        self.assertIsNone(completion.instrumentation_version)


class ProductAnalyticsQualityCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.user = User.objects.create_user(
            username='quality-user',
            email='quality-secret@example.com',
        )
        cls.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Ladder',
                'author': 'test',
                'project': cls.project,
                'requires_ticket': False,
                'is_tournament': False,
                'is_ready': True,
            },
        )
        cls.task_group = TaskGroup.objects.create(label='quality-tg')
        GameTaskGroup.objects.create(
            game=cls.game,
            task_group=cls.task_group,
            number='1',
            name='One',
        )
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            task_type='raddle',
            checker_id='equals_with_possible_spaces',
            answer='TEST',
        )

    def _bounds(self, *, days=1):
        now = timezone.now()
        return now - timedelta(days=days), now + timedelta(hours=1)

    def _call(self, since, until, **kwargs):
        return call_command(
            'check_product_analytics',
            since=since.isoformat(),
            until=until.isoformat(),
            **kwargs
        )

    def _create_clean_pair(self, *, version=2, anon_key=None):
        actor = {'anon_key': anon_key} if anon_key is not None else {'user': self.user}
        instance_id = 'ladder:{}'.format(self.task_group.pk)
        start = PlayerStartedGame.objects.create(
            **actor,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
            instrumentation_version=version,
        )
        completion = PlayerCompletedGame.objects.create(
            **actor,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
            result=PlayerCompletedGame.RESULT_SOLVED,
            instrumentation_version=version,
        )
        return start, completion

    def test_clean_mixed_legacy_and_v2_rows_pass(self):
        self._create_clean_pair(version=None, anon_key='legacy-anon-key')
        self._create_clean_pair(version=2)
        since, until = self._bounds()
        out = StringIO()

        self._call(since, until, stdout=out)

        self.assertIn('product analytics quality check passed', out.getvalue())
        self.assertNotIn('legacy-anon-key', out.getvalue())

    def test_start_before_since_satisfies_completion(self):
        start, _completion = self._create_clean_pair(version=2)
        since, until = self._bounds()
        PlayerStartedGame.objects.filter(pk=start.pk).update(
            started_at=since - timedelta(days=1)
        )
        out = StringIO()

        self._call(since, until, stdout=out)

        self.assertIn('completion_without_start\tPASS\t0', out.getvalue())

    def test_violation_raises_command_error_without_pii_or_full_anon_key(self):
        secret_anon = 'full-secret-anonymous-key-value'
        self._create_clean_pair(version=7, anon_key=secret_anon)
        since, until = self._bounds()
        out = StringIO()
        err = StringIO()

        with self.assertRaises(CommandError) as caught:
            self._call(since, until, stdout=out, stderr=err)

        rendered = out.getvalue() + err.getvalue() + str(caught.exception)
        self.assertEqual(caught.exception.returncode, 1)
        self.assertIn('start_unknown_instrumentation_version\tFAIL\t1', rendered)
        self.assertIn('completion_unknown_instrumentation_version\tFAIL\t1', rendered)
        self.assertNotIn(secret_anon, rendered)
        self.assertNotIn('quality-secret@example.com', rendered)
        self.assertNotIn('quality-user', rendered)

    def test_duplicate_logical_placement_is_detected(self):
        instance_id = 'ladder:{}'.format(self.task_group.pk)
        PlayerStartedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
            instrumentation_version=2,
        )
        PlayerStartedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id + ':malformed-duplicate',
            instrumentation_version=2,
        )
        since, until = self._bounds()
        out = StringIO()

        with self.assertRaises(CommandError):
            self._call(since, until, stdout=out)

        self.assertIn('duplicate_starts\tFAIL\t1', out.getvalue())

    def test_duplicate_logical_completion_is_detected(self):
        instance_id = 'ladder:{}'.format(self.task_group.pk)
        PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id,
            instrumentation_version=2,
        )
        PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id=instance_id + ':malformed-duplicate',
            instrumentation_version=2,
        )
        since, until = self._bounds()
        out = StringIO()

        with self.assertRaises(CommandError):
            self._call(since, until, stdout=out)

        self.assertIn('duplicate_completions\tFAIL\t1', out.getvalue())

    def test_completion_before_start_only_applies_to_live_v2_completion(self):
        start, completion = self._create_clean_pair(version=2)
        now = timezone.now()
        PlayerCompletedGame.objects.filter(pk=completion.pk).update(completed_at=now)
        PlayerStartedGame.objects.filter(pk=start.pk).update(
            started_at=now + timedelta(minutes=2)
        )
        since, until = self._bounds()
        out = StringIO()

        with self.assertRaises(CommandError):
            self._call(since, until, stdout=out)
        self.assertIn('completion_before_start\tFAIL\t1', out.getvalue())

        PlayerCompletedGame.objects.filter(pk=completion.pk).update(
            is_backfilled=True,
            instrumentation_version=None,
        )
        out = StringIO()
        self._call(since, until, stdout=out)
        self.assertIn('completion_before_start\tPASS\t0', out.getvalue())

    def test_command_does_not_modify_data(self):
        self._create_clean_pair(version=2, anon_key='readonly-anon-key')
        since, until = self._bounds()
        before_starts = list(PlayerStartedGame.objects.values().order_by('pk'))
        before_completions = list(PlayerCompletedGame.objects.values().order_by('pk'))

        self._call(since, until, stdout=StringIO())

        self.assertEqual(
            before_starts,
            list(PlayerStartedGame.objects.values().order_by('pk')),
        )
        self.assertEqual(
            before_completions,
            list(PlayerCompletedGame.objects.values().order_by('pk')),
        )

    def test_interval_validation_rejects_more_than_31_days(self):
        now = timezone.now()
        with self.assertRaisesMessage(CommandError, 'maximum of 31 days'):
            self._call(now - timedelta(days=31, seconds=1), now)

    def test_invalid_identity_and_missing_start_are_hard_failures(self):
        PlayerStartedGame.objects.create(
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id='ladder:{}'.format(self.task_group.pk),
            instrumentation_version=2,
        )
        PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='ladder',
            game_instance_id='ladder:{}'.format(self.task_group.pk),
            instrumentation_version=2,
        )
        since, until = self._bounds()
        out = StringIO()

        with self.assertRaises(CommandError):
            self._call(since, until, stdout=out)

        report = out.getvalue()
        self.assertIn('start_identity_exactly_one\tFAIL\t1', report)
        self.assertIn('completion_without_start\tFAIL\t1', report)

    def test_malformed_candidate_checks_are_hard_failures(self):
        unlinked_group = TaskGroup.objects.create(label='not-a-placement')
        row = PlayerStartedGame.objects.create(
            anon_key='malformed-candidate-key',
            game=self.game,
            task_group=unlinked_group,
            game_kind='not-ladder',
            game_instance_id='malformed',
            instrumentation_version=1,
        )
        now = timezone.now()
        PlayerStartedGame.objects.filter(pk=row.pk).update(
            started_at=now + timedelta(minutes=10)
        )
        since = now - timedelta(hours=1)
        until = now + timedelta(hours=1)
        out = StringIO()

        with self.assertRaises(CommandError):
            self._call(since, until, stdout=out)

        report = out.getvalue()
        self.assertIn('start_missing_placement\tFAIL\t1', report)
        self.assertIn('start_bad_game_instance_id\tFAIL\t1', report)
        self.assertIn('start_unknown_game_kind\tFAIL\t1', report)
        self.assertIn('start_timestamp_in_future\tFAIL\t1', report)
        self.assertIn('start_unknown_instrumentation_version\tFAIL\t1', report)
