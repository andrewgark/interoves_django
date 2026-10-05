from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0264_daily_result_projection_duration'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, max_length=128, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_team_invites', to='auth.user')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invites', to='games.team')),
            ],
            options={
                'indexes': [models.Index(fields=['team', 'revoked_at'], name='games_teami_team_id_b0c533_idx')],
            },
        ),
    ]
