from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0262_task_replacements_answer_rows'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyTaskResultProjection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_type', models.CharField(choices=[('team', 'Team'), ('user', 'Profile'), ('anon', 'Anonymous')], max_length=8)),
                ('actor_key', models.CharField(max_length=100)),
                ('anon_key', models.CharField(blank=True, max_length=64, null=True)),
                ('result_points', models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ('best_status', models.CharField(blank=True, default='', max_length=32)),
                ('best_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('attempts_count', models.PositiveIntegerField(default=0)),
                ('has_pending', models.BooleanField(default=False)),
                ('hint_numbers', models.JSONField(blank=True, default=list)),
                ('projected_at', models.DateTimeField(auto_now=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_task_result_projections', to='games.game')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_task_result_projections', to='games.task')),
                ('task_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_task_result_projections', to='games.taskgroup')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='daily_task_result_projections', to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='daily_task_result_projections', to='auth.user')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['game', 'task_group', 'actor_type', 'actor_key'], name='games_dtrp_release_actor_idx'),
                    models.Index(fields=['game', 'task_group', 'task'], name='games_dtrp_release_task_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('team__isnull', False)), fields=('game', 'task_group', 'task', 'team'), name='uniq_daily_task_proj_team'),
                    models.UniqueConstraint(condition=models.Q(('user__isnull', False)), fields=('game', 'task_group', 'task', 'user'), name='uniq_daily_task_proj_user'),
                    models.UniqueConstraint(condition=models.Q(('anon_key__isnull', False)), fields=('game', 'task_group', 'task', 'anon_key'), name='uniq_daily_task_proj_anon'),
                    models.CheckConstraint(check=models.Q(models.Q(('actor_type', 'team'), ('anon_key__isnull', True), ('team__isnull', False), ('user__isnull', True)), models.Q(('actor_type', 'user'), ('anon_key__isnull', True), ('team__isnull', True), ('user__isnull', False)), models.Q(('actor_type', 'anon'), ('anon_key__isnull', False), ('team__isnull', True), ('user__isnull', True)), _connector='OR'), name='daily_task_proj_actor_shape'),
                ],
            },
        ),
    ]
