# Раздел «Цензурки» (фаза 1: staff/support, без публичного листинга).

from django.db import migrations, models


CENSORLY_TUTORIAL_HTML = '''
<button type="button" class="new-rules-modal__close" aria-label="Закрыть" data-rules-close>×</button>
<h2 id="rules-modal-title" class="pal-title" style="margin-top:0">Цензурка</h2>
<p class="pal-lead">
  Перед вами статья русской Википедии, но почти все слова скрыты — видны только длины
  и служебные слова (предлоги, союзы, частицы). Вводите русские слова: если слово или
  его словоформа есть в тексте, откроются все такие места. Цель — открыть все слова
  в <strong>названии</strong> статьи.
</p>
'''


def add_censorly(apps, schema_editor):
    Project = apps.get_model('games', 'Project')
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    CheckerType = apps.get_model('games', 'CheckerType')

    CheckerType.objects.get_or_create(id='censorly')
    Project.objects.get_or_create(id='sections')
    project = Project.objects.get(id='sections')

    HTMLPage.objects.update_or_create(
        name='section_tutorial_censorly',
        defaults={'html': CENSORLY_TUTORIAL_HTML},
    )

    Game.objects.update_or_create(
        id='censorly',
        defaults={
            'name': 'Цензурки',
            'outside_name': 'Цензурки',
            'theme': 'Угадайте статью Википедии слово за словом',
            'project': project,
            'author': 'Interoves',
            'rules_id': None,
            'tournament_rules_id': None,
            'general_rules_id': None,
            'is_ready': False,
            'is_playable': True,
            'is_tournament': False,
            'requires_ticket': False,
            'tags': {},
        },
    )


def remove_censorly(apps, schema_editor):
    Game = apps.get_model('games', 'Game')
    HTMLPage = apps.get_model('games', 'HTMLPage')
    CheckerType = apps.get_model('games', 'CheckerType')
    Game.objects.filter(id='censorly').delete()
    HTMLPage.objects.filter(name='section_tutorial_censorly').delete()
    CheckerType.objects.filter(id='censorly').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0251_make_team_display_names_unique'),
    ]

    operations = [
        migrations.RunPython(add_censorly, remove_censorly),
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
                    ('censorly', 'Цензурка'),
                    ('grid-puzzle', 'Grid Puzzle'),
                ],
                default='default',
                max_length=100,
            ),
        ),
        migrations.CreateModel(
            name='RandomCensorlyGame',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('wiki_title', models.CharField(max_length=255, unique=True)),
                ('share_hash', models.CharField(db_index=True, max_length=32, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'task_group',
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name='random_censorly_game',
                        to='games.taskgroup',
                    ),
                ),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
