# Generated by Django 3.1.6 on 2022-06-20 10:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0068_auto_20220208_2027'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='results',
            field=models.TextField(blank=True, null=True),
        ),
    ]
