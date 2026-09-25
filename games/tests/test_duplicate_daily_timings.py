import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from django.contrib.auth.models import User

from games.models import DailySolveTiming, Game, PlayerCompletedGame, ReplaySlot, TaskGroup


class DuplicateDailyTimingAuditTests(TestCase):
    def test_read_only_audit_uses_only_canonical_namespace_and_redacts_anonymous_key(self):
        game = Game.objects.get(pk='ladder')
        task_group = TaskGroup.objects.create(label='duplicate-timing-audit')
        anon_key = 'audit-private-anonymous-identity'
        canonical = DailySolveTiming.objects.create(
            game=game, task_group=task_group, anon_key=anon_key,
            status=DailySolveTiming.STATUS_COMPLETED, frozen_ms=42000,
            accumulated_ms=42000,
        )
        replay = ReplaySlot.objects.create(
            actor_key='anon:{}'.format(anon_key), anon_key=anon_key,
            game=game, task_group=task_group,
        )
        DailySolveTiming.objects.create(
            game=game, task_group=task_group, anon_key=anon_key,
            replay_slot=replay, status=DailySolveTiming.STATUS_COMPLETED,
            frozen_ms=99000, accumulated_ms=99000,
        )

        output = StringIO()
        call_command('audit_duplicate_daily_timings', format='json', stdout=output)

        self.assertEqual(json.loads(output.getvalue()), [])
        self.assertEqual(DailySolveTiming.objects.filter(
            game=game, task_group=task_group, anon_key=anon_key,
        ).count(), 2)
        self.assertNotIn(anon_key, output.getvalue())
        self.assertTrue(DailySolveTiming.objects.filter(pk=canonical.pk).exists())

    def test_cleanup_plan_applies_only_safe_completed_running_duplicate_and_is_idempotent(self):
        game = Game.objects.get(pk='ladder')
        group = TaskGroup.objects.create(label='duplicate-timing-cleanup-safe')
        user = User.objects.create_user(username='duplicate-timing-cleanup-user')
        now = timezone.now()
        keep = DailySolveTiming.objects.create(
            game=game, task_group=group, user=user,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=42000, frozen_ms=42000, completed_at=now,
        )
        delete = DailySolveTiming.objects.create(
            game=game, task_group=group, user=user,
            status=DailySolveTiming.STATUS_RUNNING,
            accumulated_ms=0, last_seq=1,
            applied_event_ids=['race-start'], active_session_id='12345678-1234-5678-1234-567812345678',
        )
        PlayerCompletedGame.objects.create(
            game=game, task_group=group, user=user, game_kind='ladder',
            game_instance_id='cleanup-safe-instance', result=PlayerCompletedGame.RESULT_SOLVED,
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / 'plan.json'
            output = StringIO()
            call_command(
                'cleanup_duplicate_first_play_timings',
                write_plan=str(plan_path), stdout=output,
            )
            plan = json.loads(plan_path.read_text())
            self.assertEqual(len(plan['planned_deletes']), 1)
            self.assertEqual(plan['planned_deletes'][0]['keep_id'], keep.pk)
            self.assertEqual(plan['planned_deletes'][0]['delete_id'], delete.pk)
            self.assertIn('SAFE_DELETE', output.getvalue())

            output = StringIO()
            call_command(
                'cleanup_duplicate_first_play_timings',
                apply=True, plan_file=str(plan_path), stdout=output,
            )
            self.assertFalse(DailySolveTiming.objects.filter(pk=delete.pk).exists())
            self.assertTrue(DailySolveTiming.objects.filter(pk=keep.pk).exists())
            self.assertIn('deleted=1 skipped=0', output.getvalue())

            output = StringIO()
            call_command(
                'cleanup_duplicate_first_play_timings',
                apply=True, plan_file=str(plan_path), stdout=output,
            )
            self.assertIn('deleted=0 skipped=1', output.getvalue())

    def test_cleanup_plan_keeps_running_running_group_ambiguous(self):
        game = Game.objects.get(pk='ladder')
        group = TaskGroup.objects.create(label='duplicate-timing-cleanup-ambiguous')
        anon_key = 'cleanup-ambiguous-anon'
        first = DailySolveTiming.objects.create(
            game=game, task_group=group, anon_key=anon_key,
            status=DailySolveTiming.STATUS_RUNNING, last_seq=1,
        )
        second = DailySolveTiming.objects.create(
            game=game, task_group=group, anon_key=anon_key,
            status=DailySolveTiming.STATUS_RUNNING, last_seq=1,
        )
        output = StringIO()
        call_command('cleanup_duplicate_first_play_timings', stdout=output)
        self.assertIn('AMBIGUOUS', output.getvalue())
        self.assertNotIn('SAFE_DELETE', output.getvalue())
        self.assertEqual(
            DailySolveTiming.objects.filter(pk__in=(first.pk, second.pk)).count(), 2,
        )
