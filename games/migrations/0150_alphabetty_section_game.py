# Раздел «Алфавитка» (ежедневный бинарный поиск слова).

from django.db import migrations, models


ALPHABETTY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Алфавитка</h2>
<p class="pal-lead">
  Нужно угадать загаданное слово из русского словаря.
  Это нарицательное существительное в именительном падеже.
  Если ваша попытка неверна, вы можете узнать, где в алфавите относительно него находится ответ: до или после.
</p>
'''


def add_alphabetty(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    CheckerType = apps.get_model('games', 'CheckerType')

    CheckerType.objects.get_or_create(id='alphabetty')
    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')

    HTMLPage.objects.update_or_create(
        name='section_tutorial_alphabetty',
        defaults={'html': ALPHABETTY_TUTORIAL_HTML},
    )

    Game.objects.update_or_create(
        id='alphabetty',
        defaults={
            'name': 'Алфавитка',
            'outside_name': 'Алфавитка',
            'theme': 'Угадайте слово по алфавиту',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'is_ready': True,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {'alphabetty_publish_start': '2026-08-01T00:00:00+03:00'},
        },
    )


def seed_buffer(apps, schema_editor):
    """Стартовые 30 дней, если слотов ещё нет."""
    Game = apps.get_model('games', 'Game')
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    TaskGroup = apps.get_model('games', 'TaskGroup')
    Task = apps.get_model('games', 'Task')
    CheckerType = apps.get_model('games', 'CheckerType')

    if not Game.objects.filter(id='alphabetty').exists():
        return
    if GameTaskGroup.objects.filter(game_id='alphabetty').exists():
        return
    try:
        from games.alphabetty.core import pick_answer_words
    except Exception:
        return
    words = pick_answer_words(30)
    checker = CheckerType.objects.filter(id='alphabetty').first()
    if not checker or len(words) < 30:
        return
    for i, word in enumerate(words, start=1):
        tg = TaskGroup.objects.create(
            label=f'alphabetty:{i}',
            checker=checker,
            points=1,
            max_attempts=3,
        )
        Task.objects.create(
            task_group=tg,
            number='1',
            task_type='alphabetty',
            checker=checker,
            checker_data=word,
            answer=word,
            text='',
            tags={},
            points=1,
            max_attempts=None,
            is_removed=False,
        )
        GameTaskGroup.objects.create(
            game_id='alphabetty',
            task_group=tg,
            number=str(i),
            name=f'Алфавитка #{i}',
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0149_donation'),
    ]

    operations = [
        migrations.RunPython(add_alphabetty, noop),
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
                ],
                default='default',
                max_length=100,
            ),
        ),
        migrations.RunPython(seed_buffer, noop),
    ]
