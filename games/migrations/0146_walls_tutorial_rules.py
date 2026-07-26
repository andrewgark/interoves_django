# Туториал раздела «Стены»: актуальные правила игры.

from django.db import migrations


WALLS_TUTORIAL_HTML = '''<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<p class="pal-kicker">Стены</p>
<h2 id="rules-modal-title" class="pal-title">как играть</h2>
<p class="pal-lead">
  Дана таблица <strong>4×4</strong>. Она однозначно разбивается на
  <strong>4 категории по 4 слова</strong>: слова одной категории связаны общей логикой.
  Нужно найти это авторское разбиение.
</p>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      Нажимайте на клетки таблицы, чтобы выделять слова.
      Когда выделите <strong>4 слова</strong>, автоматически отправится попытка:
      «эта четвёрка есть в разбиении».
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      Если угадали — четвёрка <strong>зафиксируется</strong>.
      В турнирном режиме число попыток на угадывание категорий ограничено
      и зависит от того, сколько категорий вы уже отгадали.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      Для каждой отгаданной категории нужно написать <strong>объяснение</strong> —
      связь слов в группе. Оно должно быть достаточно точным для этого набора:
      например, «города Беларуси» лучше, чем просто «города».
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">4</div>
    <div class="pal-rule-text">
      Баллы даются за каждое угаданное разбиение и за каждое угаданное объяснение.
      Бонус — если полностью угадаете <strong>все разбиения и все объяснения</strong>.
    </div>
  </div>
</div>'''


def forwards(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_walls',
        defaults={'html': WALLS_TUTORIAL_HTML},
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0145_socialqueuepost_queued'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
