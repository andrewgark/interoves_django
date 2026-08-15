from django.db import migrations, models
from django.db.models import Count, Max


def collapse_duplicate_reactions(apps, schema_editor):
    Like = apps.get_model('games', 'Like')
    reactions = Like._default_manager
    for actor_field in ('team_id', 'user_id', 'anon_key'):
        duplicates = (
            reactions.exclude(**{'{}__isnull'.format(actor_field): True})
            .values('task_id', actor_field)
            .annotate(keep_id=Max('id'), row_count=Count('id'))
            .filter(row_count__gt=1)
        )
        for duplicate in list(duplicates):
            actor_filter = {
                'task_id': duplicate['task_id'],
                actor_field: duplicate[actor_field],
            }
            reactions.filter(**actor_filter).exclude(
                id=duplicate['keep_id']
            ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0172_playerstartedgame'),
    ]

    operations = [
        migrations.RunPython(
            collapse_duplicate_reactions,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='like',
            constraint=models.UniqueConstraint(
                fields=('task', 'team'), name='uniq_like_task_team',
            ),
        ),
        migrations.AddConstraint(
            model_name='like',
            constraint=models.UniqueConstraint(
                fields=('task', 'user'), name='uniq_like_task_user',
            ),
        ),
        migrations.AddConstraint(
            model_name='like',
            constraint=models.UniqueConstraint(
                fields=('task', 'anon_key'), name='uniq_like_task_anon',
            ),
        ),
    ]
