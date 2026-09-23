from django.db import migrations, models


def backfill_telegram_identities(apps, schema_editor):
    Profile = apps.get_model('games', 'Profile')
    SocialAccount = apps.get_model('socialaccount', 'SocialAccount')

    accounts = SocialAccount.objects.filter(provider='telegram').only(
        'user_id', 'uid', 'extra_data',
    )
    for account in accounts.iterator():
        profile = Profile.objects.filter(user_id=account.user_id).first()
        if profile is None:
            continue
        extra = account.extra_data or {}
        raw_id = extra.get('id')
        try:
            telegram_user_id = int(raw_id)
        except (TypeError, ValueError):
            telegram_user_id = None
        oidc_sub = str(account.uid or '').strip() or None
        updates = {}
        if oidc_sub and profile.telegram_oidc_sub != oidc_sub:
            updates['telegram_oidc_sub'] = oidc_sub
        # Older OIDC code stored ``sub`` in telegram_user_id. Repair that value
        # while preserving it in telegram_oidc_sub for future identity checks.
        try:
            old_oidc_uid = int(account.uid)
        except (TypeError, ValueError):
            old_oidc_uid = None
        if (
            telegram_user_id is not None
            and telegram_user_id > 0
            and (profile.telegram_user_id is None or profile.telegram_user_id == old_oidc_uid)
        ):
            updates['telegram_user_id'] = telegram_user_id
            updates['telegram_verified'] = True
        if updates:
            Profile.objects.filter(pk=profile.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0218_dailydifficulty_healthcheck'),
        ('socialaccount', '0006_alter_socialaccount_extra_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='telegram_oidc_sub',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, unique=True),
        ),
        migrations.RunPython(backfill_telegram_identities, migrations.RunPython.noop),
    ]
