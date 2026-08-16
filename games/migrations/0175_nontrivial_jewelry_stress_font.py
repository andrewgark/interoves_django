# Ударения в «Непростых украшениях» рисуем CSS, чтобы не подключать fallback-шрифт.

from django.db import migrations


RULES_PAGE_NAME = 'task_group_rules_nontrivial_jewelry'


def update_stress_markup(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    page = HTMLPage.objects.filter(pk=RULES_PAGE_NAME).first()
    if page is None:
        return

    html = page.html or ''
    html = html.replace(
        '<strong>ЧА́ЛЬ</strong>',
        '<strong class="new-ornament-rules__stress new-ornament-rules__stress--a" aria-label="ЧА́ЛЬ">ЧАЛЬ</strong>',
    )
    html = html.replace(
        '<strong>ЦО́</strong>',
        '<strong class="new-ornament-rules__stress new-ornament-rules__stress--o" aria-label="ЦО́">ЦО</strong>',
    )
    page.html = html
    page.save(update_fields=['html'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0174_nontrivial_jewelry_rules'),
    ]

    operations = [
        migrations.RunPython(update_stress_markup, noop),
    ]
