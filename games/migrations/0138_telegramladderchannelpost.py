# Generated manually for daily ladder channel posts (MTProto schedule_date).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0137_hint_number_charfield'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramLadderChannelPost',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ladder_date', models.DateField(help_text='MSK calendar date of the ladder', unique=True)),
                ('ladder_number', models.PositiveIntegerField()),
                ('play_url', models.CharField(max_length=500)),
                ('caption', models.TextField()),
                ('image_png', models.BinaryField()),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('scheduled', 'Scheduled in Telegram'),
                            ('sent', 'Sent immediately'),
                            ('failed', 'Failed'),
                        ],
                        default='scheduled',
                        max_length=16,
                    ),
                ),
                (
                    'scheduled_for',
                    models.DateTimeField(
                        blank=True,
                        help_text='Telegram schedule_date (usually 16:30 MSK)',
                        null=True,
                    ),
                ),
                ('prepared_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('telegram_message_id', models.BigIntegerField(blank=True, null=True)),
                ('error', models.TextField(blank=True, default='')),
            ],
            options={
                'ordering': ('-ladder_date',),
            },
        ),
    ]
