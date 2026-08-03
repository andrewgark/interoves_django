# Правила Алфавитки: нумерованный список.

from django.db import migrations

ALPHABETTY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Алфавитка</h2>
<div class="pal-rules">
  <div class="pal-rule">
    <div class="pal-rule-number">1</div>
    <div class="pal-rule-text">
      Загадано нарицательное существительное в именительном падеже.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">2</div>
    <div class="pal-rule-text">
      <strong>Попытайтесь его угадать</strong>, введя существующее слово.
      Валидным словом считаем любое слово из словаря, пусть даже не удовлетворяющее пункту&nbsp;1.
      Если слова в нашем словаре нет, можете предложить его туда добавить.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">3</div>
    <div class="pal-rule-text">
      <strong>Неверный ответ сужает диапазон:</strong>
      загаданное слово в алфавите либо до вашей попытки, либо после.
    </div>
  </div>
  <div class="pal-rule">
    <div class="pal-rule-number">4</div>
    <div class="pal-rule-text">
      Считаем в этом задании, что&nbsp;<strong>Ё&nbsp;=&nbsp;Е</strong>.
    </div>
  </div>
</div>
'''


def update_tutorial(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    HTMLPage.objects.update_or_create(
        name='section_tutorial_alphabetty',
        defaults={'html': ALPHABETTY_TUTORIAL_HTML},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0159_alphabetty_personal_dict_and_approved_answer'),
    ]

    operations = [
        migrations.RunPython(update_tutorial, noop),
    ]
