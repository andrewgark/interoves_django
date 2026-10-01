from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0259_daily_timing_event_audit_index'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyResultProjectionDirtyActor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_type', models.CharField(max_length=8)),
                ('actor_key', models.CharField(max_length=160)),
                ('source_revision', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_result_projection_dirty_actors', to='games.game')),
                ('task_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_result_projection_dirty_actors', to='games.taskgroup')),
            ],
            options={
                'constraints': [models.UniqueConstraint(
                    fields=('game', 'task_group', 'actor_type', 'actor_key'),
                    name='uniq_daily_proj_dirty_actor',
                )],
                'indexes': [models.Index(
                    fields=['game', 'task_group', 'source_revision'],
                    name='games_drp_dirty_release_idx',
                )],
            },
        ),
    ]
