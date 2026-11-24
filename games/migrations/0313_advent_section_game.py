# Раздел «Адвент-календарь»: одно задание в день с 1 декабря 2026 (МСК),
# и группа, которая открывает в support-консоли только этот раздел.

from django.db import migrations

ADVENT_CONSOLE_GROUP = 'Advent Calendar'


def add_advent(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    Group = apps.get_model('auth', 'Group')

    project, _ = Project.objects.get_or_create(id='sections')
    Game.objects.get_or_create(
        id='advent',
        defaults={
            'name': 'Адвент-календарь',
            'outside_name': 'Адвент-календарь',
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
            'tags': {'advent_publish_start': '2026-12-01T00:00:00+03:00'},
        },
    )
    Group.objects.get_or_create(name=ADVENT_CONSOLE_GROUP)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('games', '0312_player_first_utm_content'),
    ]

    operations = [
        migrations.RunPython(add_advent, noop),
    ]
