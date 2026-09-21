from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('games', '0217_daily_solve_timing_team')]

    operations = [
        migrations.AddField(
            model_name='dailydifficultyqueuestatus',
            name='last_health_check_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
