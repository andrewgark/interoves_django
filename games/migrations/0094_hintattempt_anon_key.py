from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0093_attempt_anon_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='hintattempt',
            name='anon_key',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]

