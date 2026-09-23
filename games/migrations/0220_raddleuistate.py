from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0219_profile_telegram_oidc_sub'),
    ]

    operations = [
        migrations.CreateModel(
            name='RaddleUiState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anon_key', models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ('game_mode', models.CharField(max_length=20)),
                ('replay_run_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('actor_key', models.CharField(editable=False, max_length=128)),
                ('namespace_key', models.CharField(editable=False, max_length=128)),
                ('drafts', models.JSONField(blank=True, default=dict)),
                ('clue_marks', models.JSONField(blank=True, default=dict)),
                ('revision', models.PositiveBigIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='raddle_ui_states', to='games.game')),
                ('replay_slot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='raddle_ui_states', to='games.replayslot')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='raddle_ui_states', to='games.task')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='raddle_ui_states', to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='raddle_ui_states', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['game', 'task', 'game_mode'], name='games_raddle_ui_game_task_idx'),
                    models.Index(fields=['replay_slot', 'replay_run_id', 'task', 'game'], name='games_raddle_ui_replay_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('actor_key', 'task', 'game', 'game_mode', 'namespace_key'), name='unique_raddle_ui_actor_context'),
                    models.UniqueConstraint(condition=models.Q(('team__isnull', False)), fields=('team', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'), name='unique_raddle_ui_team_game'),
                    models.UniqueConstraint(condition=models.Q(('user__isnull', False)), fields=('user', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'), name='unique_raddle_ui_user_game'),
                    models.UniqueConstraint(condition=models.Q(('anon_key__isnull', False)), fields=('anon_key', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'), name='unique_raddle_ui_anon_game'),
                    models.CheckConstraint(check=(models.Q(('team__isnull', False), ('user__isnull', True), ('anon_key__isnull', True)) | models.Q(('team__isnull', True), ('user__isnull', False), ('anon_key__isnull', True)) | models.Q(('team__isnull', True), ('user__isnull', True), ('anon_key__isnull', False))), name='raddle_ui_exactly_one_actor'),
                ],
            },
        ),
    ]
