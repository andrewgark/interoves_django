import re

from django.db import migrations, models

# Frozen copy of games.puzzles.salad.rules.theme_from_text at the time of this migration.
_TITLE_RE = re.compile(r'^(?:Словесный\s+)?Салат(?:ик)?\s*#\s*\d+$', re.IGNORECASE)


def _theme_from_text(value):
    theme = (value or '').strip()
    if not theme or _TITLE_RE.match(theme):
        return ''
    theme = ' '.join(theme.split())
    if theme.lower().startswith('тема:'):
        theme = theme[5:].strip()
    return theme


def move_word_salad_theme(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    tasks = list(Task.objects.filter(task_type='word_salad').exclude(text__isnull=True).exclude(text=''))
    for task in tasks:
        task.theme = _theme_from_text(task.text)[:500]
        task.text = ''
    Task.objects.bulk_update(tasks, ['theme', 'text'], batch_size=500)


def restore_word_salad_theme(apps, schema_editor):
    Task = apps.get_model('games', 'Task')
    tasks = list(Task.objects.filter(task_type='word_salad').exclude(theme=''))
    for task in tasks:
        task.text = task.theme
    Task.objects.bulk_update(tasks, ['text'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0293_next_game_autumn_vote'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='text_is_important',
            field=models.BooleanField(default=False, help_text='Текст задания влияет на решение: Лесенка и Салатик выделяют его рамкой со знаком «Внимание!».', verbose_name='Важное замечание'),
        ),
        migrations.AddField(
            model_name='task',
            name='theme',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Тема'),
        ),
        migrations.RunPython(move_word_salad_theme, restore_word_salad_theme),
    ]
