# Ensure CheckerType rows exist for all checker IDs used in games.check.CheckerFactory.

from django.db import migrations

CHECKER_IDS = [
    'equals',
    'equals_with_possible_spaces',
    'white_gray_black_list',
    'metagram_checker',
    'norm_matcher',
    'wall',
    'hangman_letters',
    'solutions_tag_number',
    'regexp',
    'any_answer',
    'number_with_error',
    'long_string',
    'antiwordle',
    'several_answers',
]


def ensure_checker_types(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    for checker_id in CHECKER_IDS:
        CheckerType.objects.get_or_create(id=checker_id)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0088_sections_project_and_palindromes_game'),
    ]

    operations = [
        migrations.RunPython(ensure_checker_types, noop),
    ]
