# Add more "sections" games (hub tiles) besides palindromes.

from django.db import migrations


def create_sections_games(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')

    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')

    # "Замены"
    Game.objects.update_or_create(
        id='replacements',
        defaults={
            'name': 'Замены',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
        },
    )

    # "Стены"
    Game.objects.update_or_create(
        id='walls',
        defaults={
            'name': 'Стены',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0097_profile_timezone'),
    ]

    operations = [
        migrations.RunPython(create_sections_games, noop),
    ]

