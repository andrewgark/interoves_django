# Generated by Django 3.1.6 on 2024-12-03 15:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0076_auto_20241022_2041'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='name',
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name='game',
            name='outside_name',
            field=models.TextField(blank=True, null=True),
        ),
    ]
