from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0237_attempt_actor_scope_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wordsaladrecheckoutbox',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Ожидает отправки'),
                    ('sending', 'Отправляется'),
                    ('sent', 'Отправлено'),
                    ('cancelled', 'Отменено'),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
    ]
