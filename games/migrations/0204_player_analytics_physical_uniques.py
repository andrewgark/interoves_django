# Production already has these nine UNIQUE indexes from
# apply_player_analytics_unique_index. Keep database_operations empty so this
# migration only aligns Django state with that schema.

from django.db import migrations, models


def _unique(fields, name):
    return models.UniqueConstraint(fields=fields, name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0203_socialqueuepost_threads'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name='playerstartedgame',
                    name='uniq_started_game_team_instance',
                ),
                migrations.RemoveConstraint(
                    model_name='playerstartedgame',
                    name='uniq_started_game_user_instance',
                ),
                migrations.RemoveConstraint(
                    model_name='playerstartedgame',
                    name='uniq_started_game_anon_instance',
                ),
                migrations.AddConstraint(
                    model_name='playerstartedgame',
                    constraint=_unique(
                        ('team', 'game_instance_id'),
                        'uniq_started_game_team_instance',
                    ),
                ),
                migrations.AddConstraint(
                    model_name='playerstartedgame',
                    constraint=_unique(
                        ('user', 'game_instance_id'),
                        'uniq_started_game_user_instance',
                    ),
                ),
                migrations.AddConstraint(
                    model_name='playerstartedgame',
                    constraint=_unique(
                        ('anon_key', 'game_instance_id'),
                        'uniq_started_game_anon_instance',
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name='playercompletedgame',
                    name='uniq_completed_game_team_instance',
                ),
                migrations.RemoveConstraint(
                    model_name='playercompletedgame',
                    name='uniq_completed_game_user_instance',
                ),
                migrations.RemoveConstraint(
                    model_name='playercompletedgame',
                    name='uniq_completed_game_anon_instance',
                ),
                migrations.AddConstraint(
                    model_name='playercompletedgame',
                    constraint=_unique(
                        ('team', 'game_instance_id'),
                        'uniq_completed_game_team_instance',
                    ),
                ),
                migrations.AddConstraint(
                    model_name='playercompletedgame',
                    constraint=_unique(
                        ('user', 'game_instance_id'),
                        'uniq_completed_game_user_instance',
                    ),
                ),
                migrations.AddConstraint(
                    model_name='playercompletedgame',
                    constraint=_unique(
                        ('anon_key', 'game_instance_id'),
                        'uniq_completed_game_anon_instance',
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name='playeranalyticsstate',
                    name='uniq_player_analytics_state_team',
                ),
                migrations.RemoveConstraint(
                    model_name='playeranalyticsstate',
                    name='uniq_player_analytics_state_user',
                ),
                migrations.RemoveConstraint(
                    model_name='playeranalyticsstate',
                    name='uniq_player_analytics_state_anon',
                ),
                migrations.AddConstraint(
                    model_name='playeranalyticsstate',
                    constraint=_unique(('team',), 'uniq_player_analytics_state_team'),
                ),
                migrations.AddConstraint(
                    model_name='playeranalyticsstate',
                    constraint=_unique(('user',), 'uniq_player_analytics_state_user'),
                ),
                migrations.AddConstraint(
                    model_name='playeranalyticsstate',
                    constraint=_unique(('anon_key',), 'uniq_player_analytics_state_anon'),
                ),
            ],
        ),
    ]
