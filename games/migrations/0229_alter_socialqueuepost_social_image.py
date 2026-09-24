from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0228_wordsalad_recheck_outbox'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='social_image',
            field=models.ImageField(
                blank=True,
                help_text='Optional compact image for X, Instagram and Threads; Telegram uses image.',
                null=True,
                upload_to='social_queue/',
            ),
        ),
    ]
