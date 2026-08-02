# Правила Алфавитки: загадка — сущ. им.п.; попытки — любое нарицательное слово.

from django.db import migrations

ALPHABETTY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Алфавитка</h2>
<p class="pal-lead">
  Загадано нарицательное существительное в именительном падеже.
  Как валидную попытку принимается любое нарицательное слово из словаря.
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
        ('games', '0155_alphabetty_tutorial_any_dict_word'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
