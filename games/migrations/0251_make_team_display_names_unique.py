from django.db import migrations


def _name_key(value):
    return ' '.join((value or '').split()).casefold()


def make_team_display_names_unique(apps, schema_editor):
    Team = apps.get_model('games', 'Team')
    used = set()

    for team in Team.objects.all().order_by('name'):
        base = (team.visible_name or team.name or '').strip() or team.name
        candidate = base
        suffix = 2
        while _name_key(candidate) in used:
            candidate = '{} ({})'.format(base, suffix)
            suffix += 1
        if team.visible_name != candidate:
            Team.objects.filter(pk=team.pk).update(visible_name=candidate)
        used.add(_name_key(candidate))


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0250_gametaskgroup_deferred_number'),
    ]

    operations = [
        migrations.RunPython(make_team_display_names_unique, migrations.RunPython.noop),
    ]
