from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0176_nontrivial_jewelry_example_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='socialqueuepost',
            name='social_image',
            field=models.ImageField(
                blank=True,
                help_text='Optional compact image for X and Instagram; Telegram uses image.',
                null=True,
                upload_to='social_queue/',
            ),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='telegram_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='twitter_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='socialqueuepost',
            name='instagram_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
