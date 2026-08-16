# Ссылки на статьи Википедии в примерах «Непростых украшений».

from django.db import migrations


RULES_PAGE_NAME = 'task_group_rules_nontrivial_jewelry'

OBRUCHALNOE_URL = 'https://ru.wikipedia.org/wiki/%D0%9E%D0%B1%D1%80%D1%83%D1%87%D0%B0%D0%BB%D1%8C%D0%BD%D0%BE%D0%B5_%D0%BA%D0%BE%D0%BB%D1%8C%D1%86%D0%BE'
AHILLES_URL = 'https://ru.wikipedia.org/wiki/%D0%90%D1%85%D0%B8%D0%BB%D0%BB%D0%B5%D1%81%D0%BE%D0%B2%D0%B0_%D0%BF%D1%8F%D1%82%D0%B0'


def add_example_links(apps, schema_editor):
    HTMLPage = apps.get_model('games', 'HTMLPage')
    page = HTMLPage.objects.filter(pk=RULES_PAGE_NAME).first()
    if page is None:
        return

    html = page.html or ''
    html = html.replace(
        '<span class="pal-text">Обручальное кольцо</span>',
        '<a class="pal-text new-ornament-rules__answer-link" href="{}" target="_blank" rel="noopener">Обручальное кольцо</a>'.format(OBRUCHALNOE_URL),
    )
    html = html.replace(
        '<span class="pal-text">Ахиллесова пята</span>',
        '<a class="pal-text new-ornament-rules__answer-link" href="{}" target="_blank" rel="noopener">Ахиллесова пята</a>'.format(AHILLES_URL),
    )
    page.html = html
    page.save(update_fields=['html'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0175_nontrivial_jewelry_stress_font'),
    ]

    operations = [
        migrations.RunPython(add_example_links, noop),
    ]
