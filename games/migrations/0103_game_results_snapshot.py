from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0102_alter_task_task_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameResultsSnapshot',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(default='tournament', max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('payload', models.JSONField(default=dict)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results_snapshots', to='games.game')),
            ],
            options={
                'unique_together': {('game', 'mode')},
            },
        ),
    ]

