from django.db import migrations


OLD_TEXT = 'Первое и последнее слово уже открыты.'
NEW_TEXT = 'Первое и последнее слова уже открыты.'


def forwards(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    page = HTMLPage.objects.filter(name='section_tutorial_ladder').first()
    if page is None or OLD_TEXT not in (page.html or ''):
        return
    page.html = page.html.replace(OLD_TEXT, NEW_TEXT)
    page.save(update_fields=['html'])


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0287_ladder_rules_tutorial_examples'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
