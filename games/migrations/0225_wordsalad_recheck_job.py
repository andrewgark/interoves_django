from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [('games', '0224_chaintaskstate_replay_namespace')]

    operations = [
        migrations.CreateModel(
            name='WordSaladRecheckJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=16)),
                ('total_actors', models.PositiveIntegerField(default=0)),
                ('completed_actors', models.PositiveIntegerField(default=0)),
                ('credited_attempts', models.PositiveIntegerField(default=0)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='word_salad_recheck_jobs', to='games.game')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='word_salad_recheck_jobs', to='games.task')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='wordsaladrecheckjob', index=models.Index(fields=['task', 'game', 'status'], name='games_wsrj_task_status_idx')),
        migrations.AddIndex(model_name='wordsaladrecheckjob', index=models.Index(fields=['status', 'next_attempt_at'], name='games_wsrj_due_idx')),
    ]
