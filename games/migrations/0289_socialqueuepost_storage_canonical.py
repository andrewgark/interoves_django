# Point SocialQueuePost image fields at canonical storage (games.ops.health.storage).

from django.db import migrations, models

import games.ops.health.storage


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0288_ladder_tutorial_plural_words'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='image',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=games.ops.health.storage.social_queue_storage,
                upload_to='social_queue/',
            ),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='social_image',
            field=models.ImageField(
                blank=True,
                help_text='Optional compact image for X, Instagram and Threads; Telegram uses image.',
                null=True,
                storage=games.ops.health.storage.social_queue_storage,
                upload_to='social_queue/',
            ),
        ),
    ]
