# Fix Game "palindromes" if it was created by 0088 before rules FKs were set to null
# (e.g. on a DB where HTMLPage rows for rules did not exist).

from django.db import migrations


def fix_palindromes_rules(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    Game.objects.filter(id='palindromes').update(
        rules_id=None,
        tournament_rules_id=None,
        general_rules_id=None,
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0090_default_checker_equals_with_possible_spaces'),
    ]

    operations = [
        migrations.RunPython(fix_palindromes_rules, noop),
    ]
