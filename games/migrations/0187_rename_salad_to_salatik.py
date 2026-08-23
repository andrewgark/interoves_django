# Публичное имя раздела: «Салат» → «Салатик».

import re

from django.db import migrations, models


WORD_SALAD_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Салатик</h2>
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
  Каждый день в полночь по Москве выходит новый салатик.
  Старые всегда доступны в архиве.
</p>
'''

_OLD_GAME_TITLES = frozenset({'Салат', 'Словесный Салат', 'Словесный салат'})
_OLD_EDITION_RE = re.compile(
    r'^(?:Словесный\s+)?Салат\s*#\s*(\d+)\s*$',
    re.IGNORECASE,
)
_NEW_TITLE = 'Салатик'


def rename_salad_to_salatik(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    TaskGroup = apps.get_model('games', 'TaskGroup')

    HTMLPage.objects.update_or_create(
        name='section_tutorial_word_salad',
        defaults={'html': WORD_SALAD_TUTORIAL_HTML},
    )

    for game in Game.objects.filter(id__in=('salad', 'word_salad')):
        changed = []
        for field in ('name', 'outside_name', 'no_html_name'):
            value = (getattr(game, field) or '').strip()
            if value in _OLD_GAME_TITLES:
                setattr(game, field, _NEW_TITLE)
                changed.append(field)
        if changed:
            game.save(update_fields=changed)

    for link in GameTaskGroup.objects.filter(game_id__in=('salad', 'word_salad')):
        match = _OLD_EDITION_RE.match((link.name or '').strip())
        if not match:
            continue
        new_name = '{} #{}'.format(_NEW_TITLE, match.group(1))
        if link.name != new_name:
            link.name = new_name
            link.save(update_fields=['name'])
        if link.task_group_id:
            tg = TaskGroup.objects.filter(pk=link.task_group_id).first()
            if tg and _OLD_EDITION_RE.match((tg.label or '').strip()):
                tg.label = new_name
                tg.save(update_fields=['label'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0186_bugreport_status_fixed'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='task_type',
            field=models.CharField(
                choices=[
                    ('default', 'default'),
                    ('wall', 'wall'),
                    ('text_with_forms', 'text_with_forms'),
                    ('replacements_lines', 'replacements_lines'),
                    ('distribute_to_teams', 'distribute_to_teams'),
                    ('with_tag', 'with_tag'),
                    ('autohint', 'autohint'),
                    ('proportions', 'Пропорции'),
                    ('raddle', 'raddle'),
                    ('alphabetty', 'alphabetty'),
                    ('word_salad', 'Салатик'),
                    ('grid-puzzle', 'Grid Puzzle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
        migrations.RunPython(rename_salad_to_salatik, noop),
    ]
