# Generated by Django 2.1.5 on 2020-07-20 17:26

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0015_task_image_width'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attempt',
            name='status',
            field=models.CharField(choices=[('Ok', 'Ok'), ('Pending', 'Pending'), ('Wrong', 'Wrong')], max_length=100),
        ),
    ]
