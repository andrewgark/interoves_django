from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('games', '0279_launch_censorly')]

    operations = [
        migrations.AddField(
            model_name='gamedifficultynorm',
            name='typical_attempts',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
