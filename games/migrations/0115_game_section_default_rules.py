# Default rules modal for section list page (Game.section_default_rules → HTMLPage).

from django.db import migrations, models
import django.db.models.deletion


def forwards(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    for gid in ('palindromes', 'replacements', 'walls'):
        page_name = 'section_tutorial_' + gid
        if not HTMLPage.objects.filter(pk=page_name).exists():
            continue
        Game.objects.filter(pk=gid, section_default_rules_id__isnull=True).update(
            section_default_rules_id=page_name,
        )


def backwards(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    for gid in ('palindromes', 'replacements', 'walls'):
        Game.objects.filter(pk=gid).update(section_default_rules_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0114_task_is_removed'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='section_default_rules',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='games_section_default_rules',
                to='games.htmlpage',
                to_field='name',
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
