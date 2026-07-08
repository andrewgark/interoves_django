# Туториал: примеры подсказок «предыдущее → следующее».

from django.db import migrations


LADDER_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Лесенка</h2>
<p class="pal-lead">
  Перед вами цепочка слов. Первое и последнее слово уже открыты.
  Ваша задача — восстановить слова между ними по подсказкам.
</p>
<div class="pal-rules">
  <div class="pal-rule">
  <strong>Как читать подсказки.</strong> Каждая подсказка загадывает следующее слово из предыдущего.
  Например:
  <ul>
    <li>Столица Республики <code>____</code>. Если предыдущее слово —
      <strong class="new-raddle-clue-ref">БЕЛАРУСЬ</strong>, то следующее слово будет
      <strong class="new-raddle-clue-next">МИНСК</strong>.</li>
    <li><code>____</code> состоит из них. Если предыдущее слово —
      <strong class="new-raddle-clue-ref">ПОЕЗД</strong>, то следующее слово будет
      <strong class="new-raddle-clue-next">ВАГОН</strong>.</li>
    <li>Вместе <code>____</code> шагать по <code>...</code>ам. Если предыдущее слово —
      <strong class="new-raddle-clue-ref">ВЕСЕЛО</strong>, то следующее слово будет
      <strong class="new-raddle-clue-next">ПРОСТОР</strong>.</li>
  </ul>
  </div>
  <div class="pal-rule">
  <strong>Как играть.</strong> Подсказки перемешаны. Вам нужно понять, какие подсказки относятся
  к каким парам соседних слов, и отгадать всю цепочку с начала или с конца.
  Угадывать можно только крайние нерешённые слова (слева и справа) — угадали, открывается следующая ступенька.
  </div>
  <div class="pal-rule">
  <strong>Лесенка дня.</strong> Каждый день в полночь по Москве выходит новая лесенка.
  Старые всегда доступны в архиве.
  </div>
</div>
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
        ('games', '0131_ladder_tutorial_hint_button'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
