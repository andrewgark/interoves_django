# Generated manually

import django.db.models.deletion
from django.db import migrations, models


def forwards_fill_memberships(apps, schema_editor):
    Profile = apps.get_model('games', 'Profile')
    ProfileTeamMembership = apps.get_model('games', 'ProfileTeamMembership')
    for p in Profile.objects.exclude(team_on=None).iterator():
        ProfileTeamMembership.objects.get_or_create(profile_id=p.pk, team_id=p.team_on_id)


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0104_alter_gameresultssnapshot_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProfileTeamMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='team_memberships',
                        to='games.profile',
                    ),
                ),
                (
                    'team',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='member_links',
                        to='games.team',
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name='profileteammembership',
            constraint=models.UniqueConstraint(
                fields=('profile', 'team'),
                name='games_profileteammembership_uniq_profile_team',
            ),
        ),
        migrations.RunPython(forwards_fill_memberships, backwards_noop),
        migrations.AlterField(
            model_name='profile',
            name='team_on',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='primary_profiles',
                to='games.team',
            ),
        ),
    ]
