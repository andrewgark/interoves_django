# MySQL builds this index after Daphne is up; see the postdeploy hook.
# Keeping database_operations empty makes the rolling deploy migration instant.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0193_dailydifficultyqueuestatus'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='attempt',
                    index=models.Index(
                        fields=['skip', 'time'],
                        name='games_attempt_skip_time_idx',
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
