from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0241_account_merge_job'),
    ]

    operations = [
        migrations.AddField(
            model_name='accountmergejob',
            name='next_url',
            field=models.URLField(blank=True, default='', max_length=2048),
        ),
    ]
