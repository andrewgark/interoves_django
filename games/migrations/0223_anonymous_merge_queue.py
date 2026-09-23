from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('games', '0222_gameauthor'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnonymousMergeJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('anon_key', models.CharField(db_index=True, max_length=64, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=16)),
                ('stage', models.CharField(choices=[('analyzing', 'Подготовка'), ('moving', 'Перенос данных'), ('reconciling', 'Проверка прогресса'), ('finalizing', 'Завершение')], default='analyzing', max_length=16)),
                ('move_step', models.CharField(blank=True, default='', max_length=32)),
                ('total_submissions', models.PositiveIntegerField(default=0)),
                ('moved_submissions', models.PositiveIntegerField(default=0)),
                ('moved_counts', models.JSONField(blank=True, default=dict)),
                ('total_reconciliation_units', models.PositiveIntegerField(default=0)),
                ('completed_reconciliation_units', models.PositiveIntegerField(default=0)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('event_recorded', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='anonymous_merge_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
                'indexes': [
                    models.Index(fields=['status', 'next_attempt_at'], name='games_amj_due_idx'),
                    models.Index(fields=['user', 'status'], name='games_amj_user_status_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='AnonymousMergeReconcileItem',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=16)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='anonymous_merge_reconcile_items', to='games.game')),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reconcile_items', to='games.anonymousmergejob')),
                ('task_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='anonymous_merge_reconcile_items', to='games.taskgroup')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('job', 'game', 'task_group'), name='games_amri_job_game_group_uniq'),
                ],
                'indexes': [
                    models.Index(fields=['job', 'status', 'next_attempt_at'], name='games_amri_due_idx'),
                ],
            },
        ),
    ]
