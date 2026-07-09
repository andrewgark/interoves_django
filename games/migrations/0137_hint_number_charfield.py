import re

import django.core.validators
from django.db import migrations, models

_RADDLE_ASSIST_CLUE_DESC = re.compile(r'^raddle_clue:(\d+)$', re.I)
_RADDLE_ASSIST_ANSWER_DESC = re.compile(r'^raddle_answer:(\d+)$', re.I)


def _convert_hint_number(number, desc):
    desc = (desc or '').strip()
    m = _RADDLE_ASSIST_CLUE_DESC.match(desc)
    if m:
        return '{}.{}'.format(1, m.group(1))
    m = _RADDLE_ASSIST_ANSWER_DESC.match(desc)
    if m:
        return '{}.{}'.format(2, m.group(1))

    if number is None:
        return None
    s = str(number).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        if 1000 < n < 2000:
            return '1.{}'.format(n - 1000)
        if 2000 < n < 3000:
            return '2.{}'.format(n - 2000)
    return s


def forwards_hint_numbers(apps, schema_editor):
    Hint = apps.get_model('games', 'Hint')
    for hint in Hint.objects.all().iterator():
        new_number = _convert_hint_number(hint.number, hint.desc)
        if new_number != hint.number:
            hint.number = new_number
            hint.save(update_fields=['number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0136_telegramgameannouncement'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hint',
            name='number',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.RunPython(forwards_hint_numbers, noop_reverse),
        migrations.AlterField(
            model_name='hint',
            name='number',
            field=models.CharField(
                blank=True,
                max_length=20,
                null=True,
                validators=[
                    django.core.validators.RegexValidator(
                        regex=re.compile(r'^\d+(?:\.\d+)?$'),
                        message='Номер подсказки: целое число или число с точкой (например 1 или 1.5).',
                    ),
                ],
            ),
        ),
    ]
