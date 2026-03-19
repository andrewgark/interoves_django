# Add CheckerType for replacements_lines task type.

from django.db import migrations


def add_checker_type(apps, schema_editor):
    CheckerType = apps.get_model('games', 'CheckerType')
    CheckerType.objects.get_or_create(id='replacements_lines')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0099_section_tutorial_html_pages'),
    ]

    operations = [
        migrations.RunPython(add_checker_type, noop),
    ]
