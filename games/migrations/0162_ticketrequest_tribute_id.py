from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0161_alphabetty_points_10'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketrequest',
            name='tribute_id',
            field=models.TextField(blank=True, null=True),
        ),
    ]
