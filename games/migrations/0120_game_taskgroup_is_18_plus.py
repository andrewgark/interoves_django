from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0119_corporategameorder'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='is_18_plus',
            field=models.BooleanField(
                default=False,
                help_text='Контент для лиц старше 18 лет: при открытии игры показывается подтверждение возраста.',
                verbose_name='18+',
            ),
        ),
        migrations.AddField(
            model_name='taskgroup',
            name='is_18_plus',
            field=models.BooleanField(
                default=False,
                help_text='Контент для лиц старше 18 лет: при открытии набора заданий показывается подтверждение возраста.',
                verbose_name='18+',
            ),
        ),
    ]
