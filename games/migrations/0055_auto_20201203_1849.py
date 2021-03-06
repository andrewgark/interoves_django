# Generated by Django 2.2.13 on 2020-12-03 15:49

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0054_auto_20201203_1722'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticketrequest',
            name='money',
            field=models.IntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='ticketrequest',
            name='tickets',
            field=models.IntegerField(default=0, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)]),
        ),
    ]
