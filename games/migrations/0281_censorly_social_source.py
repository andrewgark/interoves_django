from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0280_censorly_difficulty_attempts')]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='source',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual'),
                    ('ladder', 'Ladder'),
                    ('game', 'Game'),
                    ('word_salad', 'Word salad'),
                    ('censorly', 'Censorly'),
                ],
                default='manual',
                max_length=16,
            ),
        ),
    ]
