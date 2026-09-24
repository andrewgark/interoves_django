from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0226_wordsalad_recheck_items')]

    operations = [
        migrations.CreateModel(
            name='QueueWorkerHeartbeat',
            fields=[
                ('queue_name', models.CharField(max_length=80, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('running', 'Выполняется'), ('success', 'Успешно'), ('failed', 'Ошибка')], default='running', max_length=16)),
                ('worker', models.CharField(blank=True, default='', max_length=255)),
                ('host', models.CharField(blank=True, default='', max_length=255)),
                ('pid', models.PositiveIntegerField(blank=True, null=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('processed_count', models.IntegerField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'heartbeat очереди',
                'verbose_name_plural': 'heartbeat очередей',
            },
        ),
    ]
