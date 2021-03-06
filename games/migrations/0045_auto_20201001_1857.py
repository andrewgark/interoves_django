# Generated by Django 2.2.13 on 2020-10-01 15:57

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0044_auto_20201001_1849'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='games', to='games.Project'),
        ),
        migrations.AddField(
            model_name='team',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='teams', to='games.Project'),
        ),
    ]
