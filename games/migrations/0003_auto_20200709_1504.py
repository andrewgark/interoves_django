# Generated by Django 2.1.5 on 2020-07-09 12:04

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0009_alter_user_last_name_max_length'),
        ('games', '0002_auto_20200709_1411'),
    ]

    operations = [
        migrations.CreateModel(
            name='Profile',
            fields=[
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='profile', serialize=False, to=settings.AUTH_USER_MODEL)),
                ('first_name', models.TextField()),
                ('last_name', models.TextField()),
                ('avatar_url', models.TextField()),
            ],
            options={
                'verbose_name_plural': 'User Profiles',
            },
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('name', models.TextField(primary_key=True, serialize=False)),
            ],
        ),
        migrations.AddField(
            model_name='profile',
            name='team_on',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='users_on', to='games.Team'),
        ),
        migrations.AddField(
            model_name='profile',
            name='team_requested',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='users_requested', to='games.Team'),
        ),
    ]