from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0194_attempt_skip_time_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='playercompletedgame',
            name='instrumentation_version',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='playerstartedgame',
            name='instrumentation_version',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
