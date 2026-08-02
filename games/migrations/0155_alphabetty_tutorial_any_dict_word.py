# Правила Алфавитки: попытки — любые слова из словаря.

from django.db import migrations

ALPHABETTY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Алфавитка</h2>
<p class="pal-lead">
  Нужно угадать загаданное слово.
  Это нарицательное существительное в именительном падеже.
  В качестве попыток можно вводить любые слова из словаря — не только существительные.
  Если попытка неверна, вы узнаете, где в алфавите относительно неё находится ответ: до или после.
  Буква Ё приравнивается к букве Е.
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
        ('games', '0154_alphabetty_tutorial_yo_as_ye'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
