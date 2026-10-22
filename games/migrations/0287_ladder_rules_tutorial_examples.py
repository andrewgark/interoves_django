# Правила Лесенки: примеры — та же цепочка, что в обучающей Лесенке
# (ПЕРВАЯ → ПЯТАЯ → ЗАПЯТАЯ → ТОЧКА → ЛАСТОЧКА), по порядку.
# Меняется только абзац «Например» и таблица; остальной текст правил
# (могли править в админке) остаётся как есть.

import re

from django.db import migrations


EXAMPLES_RE = re.compile(
    r'<p class="pal-lead">\s*Каждая подсказка загадывает следующее слово из предыдущего\..*?</table>',
    re.S,
)

TUTORIAL_EXAMPLES_HTML = '''<p class="pal-lead">
  Каждая подсказка загадывает следующее слово из предыдущего: на месте <code>____</code>
  стоит предыдущее слово, на месте <code>...</code> — следующее. Например, вот вся цепочка
  обучающей Лесенки от <strong>ПЕРВАЯ</strong> до <strong>ЛАСТОЧКА</strong>:
</p>
<table class="new-raddle-tutorial-table">
  <thead>
    <tr><th>Подсказка</th><th>Объяснение</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>А и Д — это <code>____</code> и <code>...</code> буквы алфавита.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ПЕРВАЯ</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ПЯТАЯ</strong>.</td>
    </tr>
    <tr>
      <td>Знак препинания, заканчивающийся на <code>____</code>.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ПЯТАЯ</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ЗАПЯТАЯ</strong>.</td>
    </tr>
    <tr>
      <td><code>...</code> с хвостиком — это <code>____</code>.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ЗАПЯТАЯ</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ТОЧКА</strong>.</td>
    </tr>
    <tr>
      <td>Птица, заканчивающаяся на <code>____</code>.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ТОЧКА</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ЛАСТОЧКА</strong>.</td>
    </tr>
  </tbody>
</table>'''


def forwards(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    page = HTMLPage.objects.filter(name='section_tutorial_ladder').first()
    if page is None or not EXAMPLES_RE.search(page.html or ''):
        return
    page.html = EXAMPLES_RE.sub(lambda _: TUTORIAL_EXAMPLES_HTML, page.html, count=1)
    page.save(update_fields=['html'])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0286_daily_timing_client_credit'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
