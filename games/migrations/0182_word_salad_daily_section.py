# Публичный ежедневный раздел «Салат»: правила, имя, старт 23 августа 2026.

from django.db import migrations, models


WORD_SALAD_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Салат</h2>
<p class="pal-lead">
  Дана сетка <strong>4×4</strong> из букв и список слов одной темы.
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
      Справа — маски слов и их длины. Нашли слово — оно открывается.
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

PUBLISH_START = '2026-08-23T00:00:00+03:00'
RULES_PAGE = 'section_tutorial_word_salad'


def add_word_salad_section(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    CheckerType = apps.get_model('games', 'CheckerType')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    TaskGroup = apps.get_model('games', 'TaskGroup')

    CheckerType.objects.get_or_create(id='word_salad')
    Project.objects.get_or_create(id='sections', defaults={'name': 'sections'})
    project = Project.objects.get(id='sections')

    HTMLPage.objects.update_or_create(
        name=RULES_PAGE,
        defaults={'html': WORD_SALAD_TUTORIAL_HTML},
    )

    game, created = Game.objects.update_or_create(
        id='word_salad',
        defaults={
            'name': 'Салат',
            'outside_name': 'Салат',
            'no_html_name': 'Салат',
            'theme': 'Сетка 4×4: найдите все слова по соседним буквам.',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'section_default_rules_id': RULES_PAGE,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
        },
    )
    tags = dict(game.tags or {})
    if not tags.get('word_salad_publish_start'):
        tags['word_salad_publish_start'] = PUBLISH_START
        game.tags = tags
        game.save(update_fields=['tags'])

    if created:
        return

    for link in GameTaskGroup.objects.filter(game_id='word_salad').select_related('task_group'):
        name = (link.name or '').strip()
        if name.lower().startswith('словесный салат'):
            suffix = name.split('#', 1)[-1].strip() if '#' in name else str(link.number)
            new_name = 'Салат #{}'.format(suffix)
            link.name = new_name
            link.save(update_fields=['name'])
            if link.task_group_id:
                TaskGroup.objects.filter(pk=link.task_group_id).update(label=new_name)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0181_bugreportmessage'),
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
                    ('word_salad', 'Салат'),
                    ('grid-puzzle', 'Grid Puzzle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
        migrations.RunPython(add_word_salad_section, noop),
    ]
