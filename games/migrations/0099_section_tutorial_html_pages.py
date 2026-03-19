# Туториалы разделов (Палиндромы, Замены, Стены) — контент модалки «Правила» из HTMLPage.

from django.db import migrations


def create_section_tutorial_pages(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')

    palindromes_html = '''<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<p class="pal-kicker">Палиндромы</p>
<h2 id="rules-modal-title" class="pal-title">почти палиндром</h2>
<p class="pal-lead">
  Перед вами почти палиндром: фразу испортили подбором синонимов.
  Ваша цель — восстановить настоящий палиндром.
</p>
<div class="pal-example-box">
  <h3 class="pal-example-title">пример</h3>
  <div class="pal-example-grid">
    <div class="pal-example-item">
      <div class="pal-label">в задании (подсказка)</div>
      <div class="pal-text">Правитель Афродите не мешал</div>
    </div>
    <div class="pal-example-item">
      <div class="pal-label">правильная фраза-палиндром</div>
      <div class="pal-text">Лидер Венере не вредил</div>
    </div>
  </div>
  <p class="pal-note">
    Слова заменены по смыслу
    (<em>правитель → лидер</em>, <em>Афродита → Венера</em>),
    а сама фраза читается зеркально по буквам.
  </p>
</div>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      Настоящая фраза — <strong>палиндром по буквам</strong>:
      если убрать пробелы, она читается одинаково слева направо и справа налево.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      В задании <strong>каждое слово заменено</strong> на другое, близкое по смыслу.
      <strong>Порядок слов</strong> в исходной фразе при этом не менялся.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      Не обязательно менять все слова:
      иногда уместно <strong>оставить предлог или короткое слово как есть</strong>.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">4</div>
    <div class="pal-rule-text">
      Можно использовать <strong>однокоренные слова</strong>.
      Ответ <strong>с запятыми или без</strong> — оба варианта обычно принимаются.
    </div>
  </div>
</div>'''

    replacements_html = '''<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<p class="pal-kicker">Замены</p>
<h2 id="rules-modal-title" class="pal-title">как играть</h2>
<p class="pal-lead">
  В заданиях этого раздела нужно превратить одно слово или фразу в другую, делая <strong>замены</strong> по указанным правилам.
</p>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      В каждом задании даны <strong>исходное и целевое</strong> выражение. Нужно перейти от одного к другому, меняя по одному элементу за раз (букву, слог, слово — в зависимости от формулировки).
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      Правила замен (что именно можно менять и как) указаны в условии группы или в самом задании.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      Ответом обычно является <strong>целевая фраза или слово</strong> в правильном виде. Следуйте подсказкам в задании.
    </div>
  </div>
</div>'''

    walls_html = '''<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<p class="pal-kicker">Стены</p>
<h2 id="rules-modal-title" class="pal-title">как играть</h2>
<p class="pal-lead">
  «Стена» — это набор слов или фраз, разбитых на группы по общему принципу. Ваша цель — найти этот принцип и дать ответ по условию задания.
</p>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      Элементы в стене объединены в <strong>несколько групп</strong> (рядов, столбцов или блоков). У каждой группы есть общий признак — тема, категория, правило.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      Нужно понять, по какому принципу проведено разбиение, и вписать ответ: название группы, недостающий элемент, связующую фразу или то, что просят в условии.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      Условие группы или подсказки в задании подсказывают формат ответа. Запятые, порядок слов и правописание могут учитываться при проверке.
    </div>
  </div>
</div>'''

    HTMLPage.objects.update_or_create(
        name='section_tutorial_palindromes',
        defaults={'html': palindromes_html},
    )
    HTMLPage.objects.update_or_create(
        name='section_tutorial_replacements',
        defaults={'html': replacements_html},
    )
    HTMLPage.objects.update_or_create(
        name='section_tutorial_walls',
        defaults={'html': walls_html},
    )


def drop_section_tutorial_pages(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.filter(
        name__in=(
            'section_tutorial_palindromes',
            'section_tutorial_replacements',
            'section_tutorial_walls',
        ),
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0098_sections_games_replacements_walls'),
    ]

    operations = [
        migrations.RunPython(create_section_tutorial_pages, drop_section_tutorial_pages),
    ]
