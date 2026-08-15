from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def mark_existing_analytics_as_backfilled(apps, schema_editor):
    PlayerCompletedGame = apps.get_model('games', 'PlayerCompletedGame')
    PlayerAnalyticsState = apps.get_model('games', 'PlayerAnalyticsState')
    PlayerCompletedGame.objects.update(is_backfilled=True)
    PlayerAnalyticsState.objects.filter(activated_at__isnull=False).update(
        activation_is_backfilled=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0171_grid_puzzle_shading'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='playercompletedgame',
            name='is_backfilled',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='playercompletedgame',
            name='metrika_acked_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='playeranalyticsstate',
            name='activation_goal_acked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='playeranalyticsstate',
            name='activation_is_backfilled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='playeranalyticsstate',
            name='signup_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='playeranalyticsstate',
            name='signup_goal_acked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='playeranalyticsstate',
            name='signup_method',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='checkout_goal_acked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='PlayerStartedGame',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('anon_key', models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ('game_kind', models.CharField(db_index=True, max_length=100)),
                ('game_instance_id', models.CharField(db_index=True, max_length=128)),
                ('public_game_id', models.CharField(blank=True, default='', max_length=128)),
                ('started_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('metrika_acked_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('is_backfilled', models.BooleanField(db_index=True, default=False)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='started_games', to='games.game')),
                ('task_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='started_games', to='games.taskgroup')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='started_games', to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='started_games', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'начатая игра игрока',
                'verbose_name_plural': 'начатые игры игроков',
                'ordering': ['-started_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='playerstartedgame',
            constraint=models.UniqueConstraint(condition=models.Q(team__isnull=False), fields=('team', 'game_instance_id'), name='uniq_started_game_team_instance'),
        ),
        migrations.AddConstraint(
            model_name='playerstartedgame',
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=False), fields=('user', 'game_instance_id'), name='uniq_started_game_user_instance'),
        ),
        migrations.AddConstraint(
            model_name='playerstartedgame',
            constraint=models.UniqueConstraint(condition=models.Q(anon_key__isnull=False), fields=('anon_key', 'game_instance_id'), name='uniq_started_game_anon_instance'),
        ),
        migrations.AddIndex(
            model_name='playerstartedgame',
            index=models.Index(fields=['team', 'started_at'], name='games_psg_team_start_idx'),
        ),
        migrations.AddIndex(
            model_name='playerstartedgame',
            index=models.Index(fields=['user', 'started_at'], name='games_psg_user_start_idx'),
        ),
        migrations.AddIndex(
            model_name='playerstartedgame',
            index=models.Index(fields=['anon_key', 'started_at'], name='games_psg_anon_start_idx'),
        ),
        migrations.AddIndex(
            model_name='playerstartedgame',
            index=models.Index(fields=['game_kind', 'started_at'], name='games_psg_kind_start_idx'),
        ),
        migrations.RunPython(
            mark_existing_analytics_as_backfilled,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
