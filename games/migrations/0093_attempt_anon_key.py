from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0092_attempts_personal_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='attempt',
            name='anon_key',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]

