from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0105_profile_team_membership'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='join_accept_as_primary',
            field=models.BooleanField(default=True),
        ),
    ]
