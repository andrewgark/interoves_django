from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0309_subscription_gift_admin_alert_outbox'),
    ]

    operations = [
        migrations.CreateModel(
            name='ThreadsToken',
            fields=[
                ('singleton_id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ('access_token', models.TextField()),
                ('seed_token', models.TextField(blank=True, default='')),
                ('refreshed_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Threads token',
                'verbose_name_plural': 'Threads token',
            },
        ),
    ]
