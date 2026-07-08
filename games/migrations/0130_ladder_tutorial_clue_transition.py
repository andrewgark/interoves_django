# Обновить туториал «Лесенка»: подсказки = переходы от слова к следующему.

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
  (слева и справа). Угадали — открывается следующая ступенька.
  </div>
  <div class="pal-rule">
  <strong>Подсказки.</strong> Справа — перемешанный список подсказок. Каждая подсказка описывает
  <em>переход от одного слова к следующему</em> (подсказок на одну меньше, чем слов).
  В тексте подсказки <code>____</code> — предыдущее слово перехода, <code>...</code> — следующее;
  если слота следующего нет, после угадывания дописывается «→» и следующее слово.
  Кнопка 💡 открывает (подсвечивает синим) подсказку к выбранному слову; повторное нажатие снова
  показывает её. Когда переход угадан, подсказка переходит в «использованные».
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
    Game = apps.get_model('games', 'Game')
    Game.objects.filter(id='ladder', section_default_rules_id__isnull=True).update(
        section_default_rules_id='section_tutorial_ladder',
    )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0129_alter_corporategameorder_contact_value'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
