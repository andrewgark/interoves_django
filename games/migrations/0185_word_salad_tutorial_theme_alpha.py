# Салат: в правилах явно выделены тема слов и алфавитный порядок ответов.

from django.db import migrations


WORD_SALAD_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Салат</h2>
<p class="pal-lead">
  Дана сетка <strong>4×4</strong> из букв и список слов <strong>одной темы</strong>.
  Нужно найти все слова, проводя дорожки по клеткам.
</p>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      Проведите путь по <strong>соседним</strong> клеткам — соседство и по стороне, и по диагонали.
      В одном слове клетку нельзя использовать дважды.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      <strong>Пример.</strong> Если в сетке рядом стоят К, О и Т, слово <strong>КОТ</strong>
      собирается дорожкой К→О→Т.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      Справа — список ответов <strong>по алфавиту</strong>: маски слов и их длины.
      Нашли слово — оно открывается.
      Буквы, которые больше не нужны для оставшихся слов, пропадают с сетки.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">4</div>
    <div class="pal-rule-text">
      За каждое найденное слово — <strong>1 балл</strong>.
      Можно открыть букву подсказкой: <strong>−0,5 балла</strong> за букву.
    </div>
  </div>
</div>
<p class="pal-lead">
  Каждый день в полночь по Москве выходит новый салат.
  Старые всегда доступны в архиве.
</p>
'''


def update_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_word_salad',
        defaults={'html': WORD_SALAD_TUTORIAL_HTML},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0184_rename_word_salad_game_to_salad'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
