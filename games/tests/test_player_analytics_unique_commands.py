import io
import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from games.models import PlayerAnalyticsState


class PlayerAnalyticsUniqueCommandTests(TestCase):
    def test_preflight_json_is_read_only_and_omits_actor_values(self):
        secret_actor_value = 'private-anon-value-not-for-output'
        PlayerAnalyticsState.objects.create(anon_key=secret_actor_value)
        before = PlayerAnalyticsState.objects.count()
        stdout = io.StringIO()

        call_command(
            'preflight_player_analytics_uniques',
            format='json',
            stdout=stdout,
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(report['status'], 'PASS_DATA_CHECKS')
        self.assertEqual(PlayerAnalyticsState.objects.count(), before)
        self.assertNotIn(secret_actor_value, stdout.getvalue())
        self.assertEqual(report['free_storage_space']['status'], 'UNAVAILABLE')

    def test_preflight_human_is_aggregated_and_omits_actor_values(self):
        secret_actor_value = 'another-private-anon-value'
        PlayerAnalyticsState.objects.create(anon_key=secret_actor_value)
        stdout = io.StringIO()

        call_command('preflight_player_analytics_uniques', stdout=stdout)

        output = stdout.getvalue()
        self.assertIn('duplicate groups:', output)
        self.assertIn('identity XOR violations:', output)
        self.assertNotIn(secret_actor_value, output)

    def test_preflight_stops_on_identity_violation_without_repair(self):
        user = User.objects.create_user(username='preflight-invalid-identity')
        invalid = PlayerAnalyticsState.objects.create(
            user=user,
            anon_key='not-printed-invalid-actor',
        )
        stdout = io.StringIO()

        with self.assertRaises(CommandError):
            call_command(
                'preflight_player_analytics_uniques',
                format='json',
                stdout=stdout,
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(report['status'], 'BLOCKED_DATA')
        self.assertEqual(
            report['identity_violations']['PlayerAnalyticsState'],
            1,
        )
        invalid.refresh_from_db()
        self.assertEqual(invalid.anon_key, 'not-printed-invalid-actor')
        self.assertNotIn(invalid.anon_key, stdout.getvalue())

    def test_controlled_ddl_requires_explicit_index_and_refuses_sqlite(self):
        with self.assertRaises(CommandError):
            call_command('apply_player_analytics_unique_index')
        with self.assertRaisesMessage(
            CommandError,
            'controlled production DDL is supported only on MySQL',
        ):
            call_command(
                'apply_player_analytics_unique_index',
                index='uniq_started_game_user_instance',
            )
