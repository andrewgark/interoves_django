from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0238_wordsaladrecheckoutbox_cancelled'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='wordsaladrecheckoutbox',
            index=models.Index(
                fields=['status', 'claimed_until'],
                name='games_wsr_outbox_claim_idx',
            ),
        ),
    ]
