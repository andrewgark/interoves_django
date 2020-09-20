# Generated by Django 2.2.13 on 2020-10-01 16:02

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0045_auto_20201001_1857'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='project',
            field=models.ForeignKey(blank=True, default='main', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='games', to='games.Project'),
        ),
        migrations.AlterField(
            model_name='team',
            name='project',
            field=models.ForeignKey(blank=True, default='main', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='teams', to='games.Project'),
        ),
    ]
