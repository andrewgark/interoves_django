from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0146_walls_tutorial_rules'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='source',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual'),
                    ('ladder', 'Ladder'),
                    ('game', 'Game'),
                ],
                default='manual',
                max_length=16,
            ),
        ),
    ]
