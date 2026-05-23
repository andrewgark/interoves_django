import re

import django.core.validators
from django.db import migrations, models


def forwards_number_to_str(apps, schema_editor):
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    for row in GameTaskGroup.objects.all().iterator():
        row.number = str(row.number)
        row.save(update_fields=['number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0117_hiddenanonkey'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gametaskgroup',
            name='number',
            field=models.CharField(max_length=20),
        ),
        migrations.RunPython(forwards_number_to_str, noop_reverse),
        migrations.AlterField(
            model_name='gametaskgroup',
            name='number',
            field=models.CharField(
                max_length=20,
                validators=[
                    django.core.validators.RegexValidator(
                        regex=re.compile(r'^\d+(?:\.\d+)?$'),
                        message='Номер круга: целое число или число с точкой (например 1 или 1.5).',
                    ),
                ],
            ),
        ),
    ]
