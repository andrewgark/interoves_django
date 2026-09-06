from django.db import migrations, models


def _backfill_salad_authors(apps, schema_editor):
    WordSaladOffer = apps.get_model('games', 'WordSaladOffer')
    Task = apps.get_model('games', 'Task')
    Profile = apps.get_model('games', 'Profile')
    for offer in WordSaladOffer.objects.all().iterator():
        profile = Profile.objects.filter(user_id=offer.user_id).first()
        first = (getattr(profile, 'first_name', None) or '').strip() if profile else ''
        last = (getattr(profile, 'last_name', None) or '').strip() if profile else ''
        display = '{} {}'.format(first, last).strip()
        author = (offer.author or '').strip() or display
        if author and author != (offer.author or '').strip():
            offer.author = author
            offer.save(update_fields=['author'])
        if not author or not offer.task_group_id:
            continue
        task = Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()
        if task is None:
            continue
        tags = dict(task.tags or {})
        if tags.get('author'):
            continue
        tags['author'] = author
        task.tags = tags
        task.save(update_fields=['tags'])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0199_club_subscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='wordsaladoffer',
            name='author',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.RunPython(_backfill_salad_authors, _noop_reverse),
    ]
