# Салатик: описание списка ответов не зависит от раскладки экрана.

from django.db import migrations


PAGE_NAME = 'section_tutorial_word_salad'
OLD_TEXT = (
    'Справа — список ответов <strong>по алфавиту</strong>: '
    'маски слов и их длины.'
)
NEW_TEXT = (
    'Список ответов расположен <strong>по алфавиту</strong>: '
    'в нём показаны маски слов и их длины.'
)


def update_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    for page in HTMLPage.objects.filter(name=PAGE_NAME, html__contains=OLD_TEXT):
        page.html = page.html.replace(OLD_TEXT, NEW_TEXT)
        page.save(update_fields=['html'])


def revert_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    for page in HTMLPage.objects.filter(name=PAGE_NAME, html__contains=NEW_TEXT):
        page.html = page.html.replace(NEW_TEXT, OLD_TEXT)
        page.save(update_fields=['html'])


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0187_rename_salad_to_salatik'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, revert_tutorial),
    ]
