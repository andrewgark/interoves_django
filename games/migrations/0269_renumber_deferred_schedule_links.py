from django.db import migrations


def renumber_deferred_links(apps, schema_editor):
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')

    game_ids = (
        GameTaskGroup.objects.filter(is_deferred=True)
        .values_list('game_id', flat=True)
        .distinct()
    )
    for game_id in game_ids:
        links = list(
            GameTaskGroup.objects.filter(game_id=game_id, is_deferred=True)
        )
        links.sort(
            key=lambda link: (
                int(link.deferred_number)
                if str(link.deferred_number).isdigit() else 10**9,
                link.pk,
            )
        )
        for index, link in enumerate(links, start=1):
            if link.deferred_number != str(index):
                link.deferred_number = str(index)
        if links:
            GameTaskGroup.objects.bulk_update(links, ['deferred_number'])


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0268_censorly_public'),
    ]

    operations = [
        migrations.RunPython(renumber_deferred_links, migrations.RunPython.noop),
    ]
