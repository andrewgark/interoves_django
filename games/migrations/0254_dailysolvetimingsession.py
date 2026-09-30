from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_team_leases(apps, schema_editor):
    DailySolveTiming = apps.get_model('games', 'DailySolveTiming')
    DailySolveTimingSession = apps.get_model('games', 'DailySolveTimingSession')
    for row in DailySolveTiming.objects.filter(
        team__isnull=False,
        active_session_id__isnull=False,
    ).iterator():
        DailySolveTimingSession.objects.create(
            timing_id=row.pk,
            session_id=row.active_session_id,
            status='running',
            started_at=row.interval_started_at,
            last_heartbeat_at=row.last_heartbeat_at,
        )
        row.active_sessions_count = 1
        row.team_interval_started_at = row.interval_started_at
        row.active_session_id = None
        row.interval_started_at = None
        row.last_heartbeat_at = None
        row.save(update_fields=[
            'active_sessions_count', 'team_interval_started_at',
            'active_session_id', 'interval_started_at', 'last_heartbeat_at',
        ])


class Migration(migrations.Migration):

    dependencies = [('games', '0253_censorly_publish_start')]

    operations = [
        migrations.AddField(
            model_name='dailysolvetiming',
            name='active_sessions_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dailysolvetiming',
            name='team_interval_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='DailySolveTimingSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_id', models.UUIDField()),
                ('status', models.CharField(choices=[('running', 'Running'), ('paused', 'Paused'), ('closed', 'Closed')], default='paused', max_length=16)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('last_heartbeat_at', models.DateTimeField(blank=True, null=True)),
                ('paused_at', models.DateTimeField(blank=True, null=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('close_reason', models.CharField(blank=True, default='', max_length=32)),
                ('last_seq', models.BigIntegerField(default=0)),
                ('last_event_id', models.CharField(blank=True, default='', max_length=64)),
                ('applied_event_ids', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('timing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='games.dailysolvetiming')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('timing', 'session_id'), name='uniq_daily_timing_session')],
                'indexes': [models.Index(fields=['timing', 'status'], name='games_dt_session_status_idx')],
            },
        ),
        migrations.RunPython(copy_legacy_team_leases, migrations.RunPython.noop),
    ]
