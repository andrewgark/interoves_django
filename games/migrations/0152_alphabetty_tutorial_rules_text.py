# Обновить текст правил Алфавитки.

from django.db import migrations

ALPHABETTY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Алфавитка</h2>
<p class="pal-lead">
  Нужно угадать загаданное слово из русского словаря.
  Это нарицательное существительное в именительном падеже.
  Если ваша попытка неверна, вы можете узнать, где в алфавите относительно него находится ответ: до или после.
</p>
'''


def update_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_alphabetty',
        defaults={'html': ALPHABETTY_TUTORIAL_HTML},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0151_week_task_section_game'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
