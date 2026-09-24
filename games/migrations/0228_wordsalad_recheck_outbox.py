from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0227_queue_worker_heartbeat'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wordsaladrecheckitem',
            name='status',
            field=models.CharField(choices=[('pending', 'Ожидает обработки'), ('running', 'Выполняется'), ('completed', 'Завершено'), ('failed', 'Ошибка'), ('superseded', 'Заменено новой версией')], db_index=True, default='pending', max_length=16),
        ),
        migrations.CreateModel(
            name='WordSaladRecheckOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_revision', models.UUIDField(db_index=True)),
                ('status', models.CharField(choices=[('pending', 'Ожидает отправки'), ('sending', 'Отправляется'), ('sent', 'Отправлено')], db_index=True, default='pending', max_length=16)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('claim_token', models.UUIDField(blank=True, null=True)),
                ('claimed_until', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('next_attempt_at', models.DateTimeField(blank=True, null=True, db_index=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outbox_rows', to='games.wordsaladrecheckitem')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('item', 'task_revision'), name='games_wsr_outbox_item_revision_uniq')],
                'indexes': [models.Index(fields=['status', 'next_attempt_at'], name='games_wsr_outbox_due_idx')],
            },
        ),
    ]
