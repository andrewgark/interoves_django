from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0189_dailygamedifficulty'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramDailyReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('review_date', models.DateField(unique=True)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Telegram-проверка ежедневных заданий',
                'verbose_name_plural': 'Telegram-проверки ежедневных заданий',
                'ordering': ['-review_date'],
            },
        ),
    ]
