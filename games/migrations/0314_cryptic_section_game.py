# Раздел «Криптики» (дата запуска задаётся в support-консоли), группа его
# редакторов и чекер puzzlehunt с промежуточными ответами «Продолжайте!».

from django.db import migrations

CRYPTIC_CONSOLE_GROUP = 'Cryptics'


def add_cryptic(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    Group = apps.get_model('auth', 'Group')
    CheckerType = apps.get_model('games', 'CheckerType')

    project, _ = Project.objects.get_or_create(id='sections')
    Game.objects.get_or_create(
        id='cryptic',
        defaults={
            'name': 'Криптики',
            'outside_name': 'Криптики',
            'theme': '',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {'section_description': 'Одна хитрая словесная загадка в день'},
        },
    )
    Group.objects.get_or_create(name=CRYPTIC_CONSOLE_GROUP)
    CheckerType.objects.get_or_create(id='puzzlehunt')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('games', '0313_advent_section_game'),
    ]

    operations = [
        migrations.RunPython(add_cryptic, noop),
    ]
