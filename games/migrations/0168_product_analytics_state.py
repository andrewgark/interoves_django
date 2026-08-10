from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0167_alter_socialqueuepost_instagram_status_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PlayerAnalyticsState',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('anon_key', models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='analytics_states', to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='analytics_states', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'состояние продуктовой аналитики игрока',
                'verbose_name_plural': 'состояния продуктовой аналитики игроков',
            },
        ),
        migrations.CreateModel(
            name='PlayerCompletedGame',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('anon_key', models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ('game_kind', models.CharField(db_index=True, max_length=32)),
                ('game_instance_id', models.CharField(db_index=True, max_length=128)),
                ('public_game_id', models.CharField(blank=True, default='', max_length=128)),
                ('result', models.CharField(choices=[('solved', 'Solved'), ('completed', 'Completed'), ('failed', 'Failed')], default='completed', max_length=16)),
                ('completed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='completed_games', to='games.game')),
                ('task_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='completed_games', to='games.taskgroup')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='completed_games', to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='completed_games', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'завершённая игра игрока',
                'verbose_name_plural': 'завершённые игры игроков',
                'ordering': ['-completed_at'],
            },
        ),
        migrations.AddField(
            model_name='ticketrequest',
            name='purchase_goal_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='playeranalyticsstate',
            constraint=models.UniqueConstraint(condition=models.Q(team__isnull=False), fields=('team',), name='uniq_player_analytics_state_team'),
        ),
        migrations.AddConstraint(
            model_name='playeranalyticsstate',
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=False), fields=('user',), name='uniq_player_analytics_state_user'),
        ),
        migrations.AddConstraint(
            model_name='playeranalyticsstate',
            constraint=models.UniqueConstraint(condition=models.Q(anon_key__isnull=False), fields=('anon_key',), name='uniq_player_analytics_state_anon'),
        ),
        migrations.AddConstraint(
            model_name='playercompletedgame',
            constraint=models.UniqueConstraint(condition=models.Q(team__isnull=False), fields=('team', 'game_instance_id'), name='uniq_completed_game_team_instance'),
        ),
        migrations.AddConstraint(
            model_name='playercompletedgame',
            constraint=models.UniqueConstraint(condition=models.Q(user__isnull=False), fields=('user', 'game_instance_id'), name='uniq_completed_game_user_instance'),
        ),
        migrations.AddConstraint(
            model_name='playercompletedgame',
            constraint=models.UniqueConstraint(condition=models.Q(anon_key__isnull=False), fields=('anon_key', 'game_instance_id'), name='uniq_completed_game_anon_instance'),
        ),
        migrations.AddIndex(
            model_name='playercompletedgame',
            index=models.Index(fields=['team', 'completed_at'], name='games_playe_team_id_6d898f_idx'),
        ),
        migrations.AddIndex(
            model_name='playercompletedgame',
            index=models.Index(fields=['user', 'completed_at'], name='games_playe_user_id_1b98cf_idx'),
        ),
        migrations.AddIndex(
            model_name='playercompletedgame',
            index=models.Index(fields=['anon_key', 'completed_at'], name='games_playe_anon_ke_7287be_idx'),
        ),
        migrations.AddIndex(
            model_name='playercompletedgame',
            index=models.Index(fields=['game_kind', 'completed_at'], name='games_playe_game_ki_ff625d_idx'),
        ),
    ]
