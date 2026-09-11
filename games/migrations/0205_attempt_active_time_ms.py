from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0204_player_analytics_physical_uniques')]

    operations = [
        migrations.AddField(
            model_name='attempt',
            name='active_time_ms',
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
