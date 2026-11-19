from django.apps import AppConfig


class GamesConfig(AppConfig):
    name = 'games'

    def ready(self):
        # Import registers Django's login/logout signal handlers. The startup
        # event contains fingerprints only, never either secret itself.
        from games.site.http import auth_observability

        auth_observability.log_startup_auth_configuration()
        from allauth.socialaccount.models import SocialApp
        from django.db.models.signals import m2m_changed, post_delete, post_save
        from health_check.plugins import plugin_dir

        import games.club.billing.tribute.checks  # noqa: F401
        import games.daily.timing.signals  # noqa: F401 — public statistics eligibility cache
        from games.identity.allauth import clear_social_apps_cache

        # Admin edits of OAuth credentials reach this process's lookup cache.
        post_save.connect(clear_social_apps_cache, sender=SocialApp, dispatch_uid='social-apps-cache-save')
        post_delete.connect(clear_social_apps_cache, sender=SocialApp, dispatch_uid='social-apps-cache-delete')
        m2m_changed.connect(
            clear_social_apps_cache, sender=SocialApp.sites.through, dispatch_uid='social-apps-cache-sites',
        )
        import games.ops.analytics.identity  # noqa: F401 — logout rotation receiver
        import games.ops.social.instagram.models  # noqa: F401
        import games.ops.social.models  # noqa: F401
        import games.ops.social.threads.models  # noqa: F401
        import games.ops.telegram.models  # noqa: F401
        import games.ops.telegram.signals
        import games.puzzles.core.norm_matcher
        import games.site.http.signals  # noqa: F401
        import games.site.pages.profile.feedback  # noqa: F401
        from games.ops.health.checks import MediaStorageHealthCheck
        plugin_dir.register(MediaStorageHealthCheck)
