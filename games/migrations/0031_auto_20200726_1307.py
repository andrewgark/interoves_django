# Generated by Django 2.1.5 on 2020-07-26 10:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0030_profile_vk_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='number',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='taskgroup',
            name='view',
            field=models.CharField(choices=[('default', 'default'), ('table-3-n', 'table-3-n')], default='default', max_length=100),
        ),
    ]
