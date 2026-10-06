from django.db import migrations, models
import django.db.models.deletion


def link_daily_social_posts(apps, schema_editor):
    Post = apps.get_model('games', 'SocialQueuePost')
    Task = apps.get_model('games', 'Task')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    game_ids = {'ladder': 'ladder', 'word_salad': 'salad'}
    task_types = {'ladder': 'raddle', 'word_salad': 'word_salad'}
    for source, game_id in game_ids.items():
        posts = Post.objects.filter(source=source, ladder_number__isnull=False)
        groups = GameTaskGroup.objects.filter(game_id=game_id).values_list('number', 'task_group_id')
        groups_by_number = {number: group_id for number, group_id in groups}
        for post in posts.iterator():
            group_id = groups_by_number.get(str(post.ladder_number))
            if not group_id:
                continue
            task = Task.objects.filter(
                task_group_id=group_id, task_type=task_types[source],
            ).order_by('id').first()
            if task is None:
                task = Task.objects.filter(task_group_id=group_id).order_by('id').first()
            if task:
                Post.objects.filter(pk=post.pk).update(task_id=task.pk)


class Migration(migrations.Migration):
    dependencies = [('games', '0266_html_forms_task')]

    operations = [
        migrations.AddField(
            model_name='socialqueuepost',
            name='task',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='social_queue_posts', to='games.task',
            ),
        ),
        migrations.RunPython(link_daily_social_posts, migrations.RunPython.noop),
    ]
