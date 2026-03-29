# TaskGroup ↔ Game через GameTaskGroup; Attempt.game; ChainTaskState.game.

from django.db import migrations, models
import django.db.models.deletion


def forwards_copy_taskgroup_links(apps, schema_editor):
    TaskGroup = apps.get_model('games', 'TaskGroup')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    if GameTaskGroup._default_manager.exists():
        return
    # Раньше уникальность (game, number) не была в БД — подбираем свободный номер.
    for tg in TaskGroup._default_manager.exclude(game_id=None).order_by('pk').iterator():
        number = tg.number
        while GameTaskGroup._default_manager.filter(game_id=tg.game_id, number=number).exists():
            number += 1
        GameTaskGroup._default_manager.create(
            game_id=tg.game_id,
            task_group_id=tg.id,
            number=number,
            name=tg.name,
        )


def forwards_backfill_attempt_game(apps, schema_editor):
    Attempt = apps.get_model('games', 'Attempt')
    Task = apps.get_model('games', 'Task')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    for a in Attempt._default_manager.filter(game_id=None).exclude(task_id=None).iterator():
        try:
            task = Task.objects.get(pk=a.task_id)
            tg = TaskGroup.objects.get(pk=task.task_group_id)
        except (Task.DoesNotExist, TaskGroup.DoesNotExist, AttributeError, TypeError):
            continue
        if tg.game_id:
            a.game_id = tg.game_id
            a.save(update_fields=['game'])


def forwards_backfill_chain_game(apps, schema_editor):
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    Task = apps.get_model('games', 'Task')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    for row in ChainTaskState.objects.filter(game_id=None).exclude(task_id=None).iterator():
        try:
            task = Task.objects.get(pk=row.task_id)
            tg = TaskGroup.objects.get(pk=task.task_group_id)
        except (Task.DoesNotExist, TaskGroup.DoesNotExist, AttributeError, TypeError):
            continue
        if tg.game_id:
            row.game_id = tg.game_id
            row.save(update_fields=['game'])


def forwards_chain_game_fallback(apps, schema_editor):
    """Строки без игры (битая ссылка на задание) — привязываем к первой игре или удаляем."""
    ChainTaskState = apps.get_model('games', 'ChainTaskState')
    Game = apps.get_model('games', 'Game')
    orphan = ChainTaskState.objects.filter(game_id=None)
    if not orphan.exists():
        return
    first = Game.objects.order_by('pk').first()
    if first:
        orphan.update(game_id=first.pk)
    else:
        orphan.delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0112_registration_game_team_idx'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameTaskGroup',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('number', models.IntegerField()),
                ('name', models.CharField(max_length=100)),
                (
                    'game',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='task_group_links',
                        to='games.game',
                    ),
                ),
                (
                    'task_group',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='game_links',
                        to='games.taskgroup',
                    ),
                ),
            ],
            options={
                'ordering': ['number'],
            },
        ),
        migrations.AddConstraint(
            model_name='gametaskgroup',
            constraint=models.UniqueConstraint(
                fields=('game', 'task_group'),
                name='unique_gametaskgroup_game_taskgroup',
            ),
        ),
        migrations.AddConstraint(
            model_name='gametaskgroup',
            constraint=models.UniqueConstraint(
                fields=('game', 'number'),
                name='unique_gametaskgroup_game_number',
            ),
        ),
        migrations.RunPython(forwards_copy_taskgroup_links, noop_reverse),
        migrations.AddField(
            model_name='attempt',
            name='game',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='game_attempts',
                to='games.game',
            ),
        ),
        migrations.RunPython(forwards_backfill_attempt_game, noop_reverse),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_team',
        ),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_user',
        ),
        migrations.RemoveConstraint(
            model_name='chaintaskstate',
            name='unique_chain_state_anon_key',
        ),
        migrations.RemoveIndex(
            model_name='chaintaskstate',
            name='games_chain_team_id_ccb126_idx',
        ),
        migrations.RemoveIndex(
            model_name='chaintaskstate',
            name='games_chain_user_id_75f48f_idx',
        ),
        migrations.RemoveIndex(
            model_name='chaintaskstate',
            name='games_chain_anon_ke_1a8d9a_idx',
        ),
        migrations.AddField(
            model_name='chaintaskstate',
            name='game',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='chain_task_states',
                to='games.game',
            ),
        ),
        migrations.RunPython(forwards_backfill_chain_game, noop_reverse),
        migrations.RunPython(forwards_chain_game_fallback, noop_reverse),
        migrations.AddIndex(
            model_name='chaintaskstate',
            index=models.Index(
                fields=['team', 'task', 'game', 'game_mode'],
                name='games_chain_team_id_39d54e_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='chaintaskstate',
            index=models.Index(
                fields=['user', 'task', 'game', 'game_mode'],
                name='games_chain_user_id_3cd301_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='chaintaskstate',
            index=models.Index(
                fields=['anon_key', 'task', 'game', 'game_mode'],
                name='games_chain_anon_ke_4867f1_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('team__isnull', False)),
                fields=('team', 'task', 'game', 'game_mode'),
                name='unique_chain_state_team_game',
            ),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('user__isnull', False)),
                fields=('user', 'task', 'game', 'game_mode'),
                name='unique_chain_state_user_game',
            ),
        ),
        migrations.AddConstraint(
            model_name='chaintaskstate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('anon_key__isnull', False)),
                fields=('anon_key', 'task', 'game', 'game_mode'),
                name='unique_chain_state_anon_key_game',
            ),
        ),
        migrations.AlterField(
            model_name='chaintaskstate',
            name='game',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='chain_task_states',
                to='games.game',
            ),
        ),
        migrations.RemoveField(
            model_name='taskgroup',
            name='game',
        ),
        migrations.RemoveField(
            model_name='taskgroup',
            name='name',
        ),
        migrations.RemoveField(
            model_name='taskgroup',
            name='number',
        ),
        migrations.AddField(
            model_name='taskgroup',
            name='label',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Для списков в админке; игрокам не показывается.',
                max_length=100,
                verbose_name='Подпись (админка)',
            ),
        ),
    ]
