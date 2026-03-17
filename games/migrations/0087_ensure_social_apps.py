# Ensures django-allauth SocialApp rows exist for VK and Google on fresh/test DBs.
# Production usually already has them from admin → migration does nothing there.
# Replace client_id/secret in admin (or env-backed process) for real OAuth.

from django.db import migrations


# Placeholders so provider_login_url works; OAuth succeeds only after real keys in admin.
_PLACEHOLDER_VK_ID = "replace-vk-app-id-in-admin"
_PLACEHOLDER_VK_SECRET = "replace-vk-secret-in-admin"
_PLACEHOLDER_GOOGLE_ID = "replace-google-client-id-in-admin"
_PLACEHOLDER_GOOGLE_SECRET = "replace-google-secret-in-admin"


def ensure_social_apps(apps, schema_editor):
    SocialApp = apps.get_model("socialaccount", "SocialApp")
    Site = apps.get_model("sites", "Site")
    sites = list(Site.objects.all())
    if not sites:
        return

    specs = (
        ("vk", "VK", _PLACEHOLDER_VK_ID, _PLACEHOLDER_VK_SECRET),
        ("google", "Google", _PLACEHOLDER_GOOGLE_ID, _PLACEHOLDER_GOOGLE_SECRET),
    )
    for provider, name, client_id, secret in specs:
        if SocialApp.objects.filter(provider=provider).exists():
            continue
        app = SocialApp.objects.create(
            provider=provider,
            name=name,
            client_id=client_id,
            secret=secret,
        )
        app.sites.set(sites)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0086_ensure_main_project"),
        ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ]

    operations = [
        migrations.RunPython(ensure_social_apps, noop),
    ]
