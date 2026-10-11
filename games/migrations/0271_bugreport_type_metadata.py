from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0270_censorlyoffer'),
    ]

    operations = [
        migrations.AddField(
            model_name='bugreport',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='report_type',
            field=models.CharField(
                choices=[
                    ('BUG', 'Обычный баг-репорт'),
                    ('CENSORLY_WORD_MATCH', 'Матчинг слов Цензурки'),
                ],
                db_index=True,
                default='BUG',
                max_length=32,
            ),
        ),
    ]
