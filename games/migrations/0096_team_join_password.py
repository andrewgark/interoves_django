from django.db import migrations, models
import secrets


def backfill_team_passwords(apps, schema_editor):
    Team = apps.get_model('games', 'Team')
    for t in Team.objects.all():
        if not getattr(t, 'join_password', None):
            t.join_password = secrets.token_hex(4)
            t.save(update_fields=['join_password'])


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0095_like_personal_and_anon'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='join_password',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.RunPython(backfill_team_passwords, migrations.RunPython.noop),
    ]

