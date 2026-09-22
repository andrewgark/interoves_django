import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from games.models import DailySolveTiming, Game, ReplaySlot, TaskGroup


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
