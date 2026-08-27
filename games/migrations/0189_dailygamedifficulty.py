from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0188_word_salad_tutorial_responsive_wording'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyGameDifficulty',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('n', models.PositiveIntegerField(default=0)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('stars', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('is_preliminary', models.BooleanField(default=False)),
                ('dirty', models.BooleanField(db_index=True, default=True)),
                ('calculated_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('placement', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='difficulty', to='games.gametaskgroup')),
            ],
            options={
                'verbose_name': 'сложность ежедневной игры',
                'verbose_name_plural': 'сложность ежедневных игр',
                'ordering': ['placement__game_id', 'placement__number'],
            },
        ),
    ]
