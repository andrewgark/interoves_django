from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('games', '0091_fix_palindromes_game_rules_fks'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='attempt',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='attempts', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='hintattempt',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hint_attempts', to=settings.AUTH_USER_MODEL),
        ),
    ]

