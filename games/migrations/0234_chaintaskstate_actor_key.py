from django.db import migrations, models
from django.db.models import Count


def backfill_actor_keys_and_reject_duplicates(apps, schema_editor):
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    invalid = ChainTaskState.objects.filter(
        team_id__isnull=True, user_id__isnull=True, anon_key__isnull=True,
    ).values_list('pk', flat=True).first()
    if invalid is not None:
        raise RuntimeError(
            'Cannot backfill ChainTaskState.actor_key for row {}'.format(invalid)
        )

    # Check the future unique key before adding its constraint, then backfill
    # with one SQL UPDATE instead of 43k individual ORM UPDATE statements.
    duplicates = ChainTaskState.objects.values(
        'team_id', 'user_id', 'anon_key', 'task_id', 'game_id',
        'game_mode', 'replay_slot_key',
    ).annotate(n=Count('pk')).filter(n__gt=1).order_by(
        'team_id', 'user_id', 'anon_key', 'task_id', 'game_id',
        'game_mode', 'replay_slot_key',
    ).first()
    if duplicates is not None:
        raise RuntimeError(
            'Duplicate ChainTaskState logical key before unique constraint: {}'.format(
                duplicates,
            )
        )

    table = schema_editor.quote_name(ChainTaskState._meta.db_table)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE {} SET actor_key = CASE "
            "WHEN team_id IS NOT NULL THEN CONCAT('team:', team_id) "
            "WHEN user_id IS NOT NULL THEN CONCAT('user:', user_id) "
            "ELSE CONCAT('anon:', anon_key) END".format(table)
        )


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0233_wordsaladrecheckjob_pending_resolution'),
    ]

    operations = [
        migrations.AddField(
            model_name='chaintaskstate',
            name='actor_key',
            field=models.CharField(editable=False, max_length=128, null=True),
        ),
        migrations.RunPython(
            backfill_actor_keys_and_reject_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='chaintaskstate',
            name='actor_key',
            field=models.CharField(editable=False, max_length=128),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                fields=('actor_key', 'task', 'game', 'game_mode', 'replay_slot_key'),
                name='unique_chain_state_actor_context',
            ),
        ),
    ]
