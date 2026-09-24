from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [('games', '0225_wordsalad_recheck_job')]

    operations = [
        migrations.AddField(
            model_name='wordsaladrecheckjob',
            name='task_revision',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
        ),
        migrations.AlterField(
            model_name='wordsaladrecheckjob',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Ожидает обработки'),
                    ('running', 'Выполняется'),
                    ('completed', 'Завершено'),
                    ('failed', 'Ошибка'),
                    ('superseded', 'Заменено новой версией'),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name='WordSaladRecheckItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_key', models.CharField(max_length=255)),
                ('anon_key', models.CharField(blank=True, max_length=64, null=True)),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=16)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('credited_attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='games.wordsaladrecheckjob')),
                ('replay_slot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='games.replayslot')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='games.team')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='auth.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='wordsaladrecheckitem',
            constraint=models.UniqueConstraint(fields=('job', 'actor_key'), name='games_wsrji_job_actor_uniq'),
        ),
        migrations.AddIndex(
            model_name='wordsaladrecheckitem',
            index=models.Index(fields=('job', 'status', 'next_attempt_at'), name='games_wsrji_due_idx'),
        ),
    ]
