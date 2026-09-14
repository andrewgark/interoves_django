from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0206_word_salad_offer_rare_words'),
    ]

    operations = [
        migrations.AddField(
            model_name='wordsaladoffer',
            name='converted_from',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='converted_offers',
                to='games.wordsaladoffer',
            ),
        ),
    ]
