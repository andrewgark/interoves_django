from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0096_team_join_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='timezone',
            field=models.CharField(default='Europe/Moscow', max_length=64),
        ),
    ]

