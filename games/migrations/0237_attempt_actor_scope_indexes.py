from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0236_merge_legacy_chain_state_duplicates'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='attempt',
            index=models.Index(
                fields=['task', 'team', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_team_scope_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='attempt',
            index=models.Index(
                fields=['task', 'user', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_user_scope_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='attempt',
            index=models.Index(
                fields=['task', 'anon_key', 'replay_slot', 'game', 'skip', 'points', 'time'],
                name='games_att_task_anon_scope_idx',
            ),
        ),
    ]
