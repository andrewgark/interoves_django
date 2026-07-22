from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0142_telegramladderchannelpost_instagram'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstagramToken',
            fields=[
                ('singleton_id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ('access_token', models.TextField()),
                ('refreshed_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Instagram token',
                'verbose_name_plural': 'Instagram token',
            },
        ),
    ]
