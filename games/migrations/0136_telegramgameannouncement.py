# Generated manually for Telegram bot announcements tracking.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0135_bug_report'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramGameAnnouncement',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('start', 'Game start'), ('end_soon_30', '30 minutes before end'), ('end', 'Game end')], max_length=32)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='telegram_announcements', to='games.game')),
            ],
        ),
        migrations.AddConstraint(
            model_name='telegramgameannouncement',
            constraint=models.UniqueConstraint(fields=('game', 'kind'), name='unique_telegram_game_announcement'),
        ),
    ]
