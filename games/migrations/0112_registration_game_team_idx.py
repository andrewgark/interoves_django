# MySQL: use online DDL (INPLACE, LOCK=NONE) so EB deploy does not block.
# database_operations=[] → migrate finishes instantly; the index is created
# in the background by .platform/hooks/postdeploy/02_background_migrations.sh.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0111_backfill_chain_task_states'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='registration',
                    index=models.Index(
                        fields=['game', 'team'],
                        name='games_reg_game_team_idx',
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
