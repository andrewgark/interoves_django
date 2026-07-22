from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0141_telegramladderchannelpost_twitter'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramladderchannelpost',
            name='instagram_media_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Instagram media id if also posted at 00:15 prepare (immediate)',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='telegramladderchannelpost',
            name='instagram_error',
            field=models.TextField(blank=True, default=''),
        ),
    ]
