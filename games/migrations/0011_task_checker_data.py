# Generated by Django 2.1.5 on 2020-07-14 16:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0010_auto_20200714_1915'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='checker_data',
            field=models.TextField(default=1),
            preserve_default=False,
        ),
    ]
