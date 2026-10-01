from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0260_daily_result_projection_dirty_actor'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyresultprojection',
            name='attempts_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
