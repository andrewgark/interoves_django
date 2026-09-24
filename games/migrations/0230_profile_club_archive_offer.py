from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0229_alter_socialqueuepost_social_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='club_archive_offer_last_shown_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='club_archive_offer_never',
            field=models.BooleanField(default=False),
        ),
    ]
