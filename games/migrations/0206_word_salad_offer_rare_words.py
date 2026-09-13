from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0205_attempt_active_time_ms')]

    operations = [
        migrations.AddField(
            model_name='wordsaladoffer',
            name='rare_words_text',
            field=models.TextField(blank=True, default=''),
        ),
    ]
