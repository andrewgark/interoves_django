# Раздел «Лесенка» (ежедневные raddle-задания).

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
  <strong>Как играть.</strong> В любой момент можно угадывать только крайние нерешённые слова
  (слева и справа). Угадали — открывается следующая подсказка.
  </div>
  <div class="pal-rule">
  <strong>Подсказки.</strong> Справа — перемешанный список подсказок. Когда слово между двумя
  подсказками угадано, подсказка переходит в «использованные».
  </div>
  <div class="pal-rule">
  <strong>Лесенка дня.</strong> Каждый день в полночь по Москве выходит новая лесенка.
  Старые всегда доступны в архиве.
  </div>
</div>
'''


def create_ladder_section(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')

    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')

    HTMLPage.objects.update_or_create(
        name='section_tutorial_ladder',
        defaults={'html': LADDER_TUTORIAL_HTML},
    )

    Game.objects.update_or_create(
        id='ladder',
        defaults={
            'name': 'Лесенка',
            'outside_name': 'Лесенка',
            'theme': 'Одна лестница слов в день',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {'ladder_publish_start': '2026-07-08T00:00:00+03:00'},
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0124_ordergamereview_name_caption'),
    ]

    operations = [
        migrations.RunPython(create_ladder_section, noop),
    ]
