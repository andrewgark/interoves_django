from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0239_wordsaladrecheckoutbox_claim_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='club_archive_offer_deferred_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
