# Generated by Django 3.1.6 on 2022-06-26 11:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0069_game_results'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskgroup',
            name='view',
            field=models.CharField(choices=[('default', 'default'), ('table-3-n', 'table-3-n'), ('table-4-n', 'table-4-n')], default='default', max_length=100),
        ),
    ]
