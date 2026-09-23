from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0221_daily_projection_validity'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameAuthor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='authored_games', to='games.profile')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='authored_games', to='games.team')),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='authors', to='games.game')),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(check=models.Q(('profile__isnull', False), ('team__isnull', True)) | models.Q(('profile__isnull', True), ('team__isnull', False)), name='games_gameauthor_exactly_one_actor'),
                    models.UniqueConstraint(fields=('game', 'profile'), name='games_gameauthor_game_profile_uniq'),
                    models.UniqueConstraint(fields=('game', 'team'), name='games_gameauthor_game_team_uniq'),
                ],
            },
        ),
    ]
