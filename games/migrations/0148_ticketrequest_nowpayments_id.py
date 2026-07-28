from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0147_socialqueuepost_source_game'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketrequest',
            name='nowpayments_id',
            field=models.TextField(blank=True, null=True),
        ),
    ]
