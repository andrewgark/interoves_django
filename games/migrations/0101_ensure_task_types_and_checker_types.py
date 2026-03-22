# Все реализованные task_type и соответствующие CheckerType: одна миграция для согласованности.

from django.db import migrations

# Все варианты task_type из Task.TASK_TYPE_VARIANTS
IMPLEMENTED_TASK_TYPES = [
    'default',
    'wall',
    'text_with_forms',
    'replacements_lines',
    'distribute_to_teams',
    'with_tag',
    'autohint',
    'proportions',
]

# Все типы чекеров из games.check.CheckerFactory
CHECKER_TYPE_IDS = [
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
    'replacements_lines',
]


def ensure_checker_types(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    for cid in CHECKER_TYPE_IDS:
        CheckerType.objects.get_or_create(id=cid)


def ensure_task_types(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    allowed = set(IMPLEMENTED_TASK_TYPES)
    updated = Task.objects.exclude(task_type__in=allowed).update(task_type='default')
    if updated:
        # Для отладки при необходимости можно логировать updated
        pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0100_replacements_lines_checker_type'),
    ]

    operations = [
        migrations.RunPython(ensure_checker_types, noop),
        migrations.RunPython(ensure_task_types, noop),
    ]
