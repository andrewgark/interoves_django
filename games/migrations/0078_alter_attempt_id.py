# Generated by Django 4.2.16 on 2024-12-03 18:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0077_auto_20241203_1827'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attempt',
            name='id',
            field=models.AutoField(primary_key=True, serialize=False),
        ),
    ]