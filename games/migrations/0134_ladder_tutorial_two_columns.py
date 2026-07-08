# Туториал: примеры в виде таблицы «подсказка / объяснение»,
# без левой колонки заголовков и без правила про крайние слова.

from django.db import migrations


LADDER_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Лесенка</h2>
<p class="pal-lead">
  Перед вами цепочка слов. Первое и последнее слово уже открыты.
  Ваша задача — восстановить слова между ними по подсказкам.
</p>
<p class="pal-lead">
  Каждая подсказка загадывает следующее слово из предыдущего. Например:
</p>
<table class="new-raddle-tutorial-table">
  <thead>
    <tr><th>Подсказка</th><th>Объяснение</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Столица Республики <code>____</code>.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">БЕЛАРУСЬ</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">МИНСК</strong>.</td>
    </tr>
    <tr>
      <td><code>____</code> состоит из них.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ПОЕЗД</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ВАГОН</strong>.</td>
    </tr>
    <tr>
      <td>Вместе <code>____</code> шагать по <code>...</code>ам.</td>
      <td>Если предыдущее слово — <strong class="new-raddle-clue-ref">ВЕСЕЛО</strong>,
        то следующее слово будет <strong class="new-raddle-clue-next">ПРОСТОР</strong>.</td>
    </tr>
  </tbody>
</table>
<p class="pal-lead">
  Подсказки перемешаны. Вам нужно понять, какие подсказки относятся к каким парам
  соседних слов, и отгадать всю цепочку с начала или с конца.
</p>
<p class="pal-lead">
  Каждый день в полночь по Москве выходит новая лесенка.
  Старые всегда доступны в архиве.
</p>
'''


def forwards(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_ladder',
        defaults={'html': LADDER_TUTORIAL_HTML},
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0133_raddle_assist_fractions_half'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
