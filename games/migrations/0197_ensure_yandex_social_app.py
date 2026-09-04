# Ensures django-allauth SocialApp row exists for Yandex on fresh/test DBs.
# Production usually already has Google/VK from admin → this only inserts yandex
# if missing. Replace client_id/secret in admin for real OAuth.

from django.db import migrations


_PLACEHOLDER_YANDEX_ID = "replace-yandex-client-id-in-admin"
_PLACEHOLDER_YANDEX_SECRET = "replace-yandex-secret-in-admin"


def ensure_yandex_social_app(apps, schema_editor):
    SocialApp = apps.get_model("socialaccount", "SocialApp")
    Site = apps.get_model("sites", "Site")
    sites = list(Site.objects.all())
    if not sites:
        return
    if SocialApp.objects.filter(provider="yandex").exists():
        return
    app = SocialApp.objects.create(
        provider="yandex",
        name="Yandex",
        client_id=_PLACEHOLDER_YANDEX_ID,
        secret=_PLACEHOLDER_YANDEX_SECRET,
    )
    app.sites.set(sites)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0196_dailysolvetiming"),
        ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ]

    operations = [
        migrations.RunPython(ensure_yandex_social_app, noop),
    ]
