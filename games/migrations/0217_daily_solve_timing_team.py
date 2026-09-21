from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0216_daily_projection_adapter_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailysolvetiming',
            name='team',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='daily_solve_timings', to='games.team',
            ),
        ),
        migrations.AddField(
            model_name='dailysolvetiming',
            name='team_timing_key',
            field=models.CharField(blank=True, default='first', max_length=64),
        ),
        migrations.AddConstraint(
            model_name='dailysolvetiming',
            constraint=models.UniqueConstraint(
                fields=('team', 'game', 'task_group', 'team_timing_key'),
                name='uniq_daily_timing_team_key',
            ),
        ),
        migrations.AddConstraint(
            model_name='dailysolvetiming',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(('anon_key__isnull', True), ('team__isnull', True), ('user__isnull', False)),
                    models.Q(('anon_key__isnull', True), ('team__isnull', False), ('user__isnull', True)),
                    models.Q(('anon_key__isnull', False), ('team__isnull', True), ('user__isnull', True)),
                    _connector='OR',
                ),
                name='daily_timing_actor_shape',
            ),
        ),
        migrations.AddIndex(
            model_name='dailysolvetiming',
            index=models.Index(fields=['team', 'game'], name='games_dst_team_game_idx'),
        ),
    ]
