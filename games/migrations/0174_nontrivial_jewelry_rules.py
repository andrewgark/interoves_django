# Правила набора «Непростые украшения».

from django.db import migrations


RULES_PAGE_NAME = 'task_group_rules_nontrivial_jewelry'
TASK_GROUP_NAME = 'Непростые украшения'

RULES_HTML = '''
<div class="new-ornament-rules">
  <p class="new-ornament-rules__rhythm">
    <span class="new-ornament-rules__rhythm-word">ОБ-РУ-<strong class="new-ornament-rules__stress new-ornament-rules__stress--a" aria-label="ЧА́ЛЬ">ЧАЛЬ</strong>-НО-Е</span>
    <span class="new-ornament-rules__rhythm-word">КОЛЬ-<strong class="new-ornament-rules__stress new-ornament-rules__stress--o" aria-label="ЦО́">ЦО</strong></span>
    <span class="new-ornament-rules__rhythm-caption">— непростое украшенье</span>
  </p>

  <div class="pal-rules">
    <div class="pal-rule">
      <div class="pal-rule-number">1</div>
      <div class="pal-rule-text">
        Загадано <strong>название статьи в русской Википедии</strong>
        (без перенаправлений).
      </div>
    </div>
    <div class="pal-rule">
      <div class="pal-rule-number">2</div>
      <div class="pal-rule-text">
        Загаданная фраза должна состоять из <strong>7 слогов</strong>
        с ударениями на <strong>3-м и 7-м</strong> слогах. Иными словами,
        её можно произнести вместо фразы
        <span class="new-ornament-rules__meter">об-ру-<strong class="new-ornament-rules__stress new-ornament-rules__stress--a" aria-label="ЧА́ЛЬ">ЧАЛЬ</strong>-но-е коль-<strong class="new-ornament-rules__stress new-ornament-rules__stress--o" aria-label="ЦО́">ЦО</strong></span>,
        сохранив тот же ритм.
      </div>
    </div>
    <div class="pal-rule">
      <div class="pal-rule-number">3</div>
      <div class="pal-rule-text">
        В ответе не встречаются слова, <strong>однокоренные</strong> тем,
        которые использованы в загадке.
      </div>
    </div>
  </div>

  <div class="pal-example-box new-ornament-rules__examples">
    <h3 class="pal-example-title">Примеры</h3>
    <div class="pal-example-grid">
      <div class="pal-example-item new-ornament-rules__example">
        <div>
          <span class="pal-label">Загадка</span>
          <span class="pal-text">Подвенечный сувенир</span>
        </div>
        <span class="new-ornament-rules__arrow" aria-hidden="true">→</span>
        <div>
          <span class="pal-label">Ответ</span>
          <span class="pal-text">Обручальное кольцо</span>
        </div>
      </div>
      <div class="pal-example-item new-ornament-rules__example">
        <div>
          <span class="pal-label">Загадка</span>
          <span class="pal-text">Уязвимая нога</span>
        </div>
        <span class="new-ornament-rules__arrow" aria-hidden="true">→</span>
        <div>
          <span class="pal-label">Ответ</span>
          <span class="pal-text">Ахиллесова пята</span>
        </div>
      </div>
    </div>
  </div>
</div>
'''


def add_rules(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    TaskGroup = apps.get_model('games', 'TaskGroup')

    HTMLPage.objects.update_or_create(
        name=RULES_PAGE_NAME,
        defaults={'html': RULES_HTML},
    )

    task_group_ids = GameTaskGroup.objects.filter(
        name=TASK_GROUP_NAME,
    ).values_list('task_group_id', flat=True)
    TaskGroup.objects.filter(pk__in=task_group_ids).update(rules_id=RULES_PAGE_NAME)
    TaskGroup.objects.filter(label=TASK_GROUP_NAME).update(rules_id=RULES_PAGE_NAME)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0173_like_actor_uniqueness'),
    ]

    operations = [
        migrations.RunPython(add_rules, noop),
    ]
