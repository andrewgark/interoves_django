import secrets

from django.db import migrations, models


SCHEDULED_GAME_IDS = ('ladder', 'salad', 'alphabetty', 'week_task')


def _copy_offer_hashes(apps, schema_editor):
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    for model_name in ('WordSaladOffer', 'LadderOffer', 'AlphabettyOffer'):
        Offer = apps.get_model('games', model_name)
        offers = Offer.objects.exclude(share_hash='').exclude(accepted_link_id=None)
        for offer in offers.iterator():
            GameTaskGroup.objects.filter(
                pk=offer.accepted_link_id,
                share_hash__isnull=True,
            ).update(share_hash=offer.share_hash)


def _fill_missing_hashes(apps, schema_editor):
    GameTaskGroup = apps.get_model('games', 'GameTaskGroup')
    pending = GameTaskGroup.objects.filter(
        game_id__in=SCHEDULED_GAME_IDS,
        share_hash__isnull=True,
    )
    for link in pending.iterator():
        for _ in range(20):
            token = secrets.token_hex(8)
            if GameTaskGroup.objects.filter(share_hash=token).exists():
                continue
            GameTaskGroup.objects.filter(pk=link.pk, share_hash__isnull=True).update(
                share_hash=token,
            )
            break


def forwards(apps, schema_editor):
    _copy_offer_hashes(apps, schema_editor)
    _fill_missing_hashes(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0230_profile_club_archive_offer'),
    ]

    operations = [
        migrations.AddField(
            model_name='gametaskgroup',
            name='share_hash',
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
