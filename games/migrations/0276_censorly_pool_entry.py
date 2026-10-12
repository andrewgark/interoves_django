from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('games', '0275_hintattempt_requested_by_profile_and_more')]

    operations = [
        migrations.CreateModel(
            name='CensorlyPoolEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255, unique=True)),
                ('topics', models.JSONField(blank=True, default=list)),
                ('object_types', models.JSONField(blank=True, default=list)),
                ('difficulty', models.CharField(blank=True, default='', max_length=16)),
                ('enabled', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['title']},
        ),
    ]
