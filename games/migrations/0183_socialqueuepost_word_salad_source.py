from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0182_word_salad_daily_section'),
    ]

    operations = [
        migrations.AlterField(
            model_name='socialqueuepost',
            name='source',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual'),
                    ('ladder', 'Ladder'),
                    ('game', 'Game'),
                    ('word_salad', 'Word salad'),
                ],
                default='manual',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='socialqueuepost',
            name='ladder_date',
            field=models.DateField(
                blank=True,
                help_text='MSK calendar date for daily teasers (unique together with source)',
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name='socialqueuepost',
            constraint=models.UniqueConstraint(
                fields=('source', 'ladder_date'),
                name='games_socialqueuepost_source_ladder_date_uniq',
            ),
        ),
    ]
