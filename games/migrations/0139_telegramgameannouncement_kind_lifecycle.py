# Alter TelegramGameAnnouncement.kind for extended chat lifecycle kinds.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0138_telegramladderchannelpost'),
    ]

    operations = [
        migrations.AlterField(
            model_name='telegramgameannouncement',
            name='kind',
            field=models.CharField(max_length=64),
        ),
    ]
