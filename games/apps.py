from django.apps import AppConfig


class GamesConfig(AppConfig):
    name = 'games'

    def ready(self):
        # Import registers Django's login/logout signal handlers. The startup
        # event contains fingerprints only, never either secret itself.
        from games.site.http import auth_observability

        auth_observability.log_startup_auth_configuration()
        import games.club.billing.tribute.checks  # noqa: F401
        import games.site.http.signals  # noqa: F401
        import games.daily.timing.signals  # noqa: F401 — public statistics eligibility cache
        import games.ops.analytics.identity  # noqa: F401 — logout rotation receiver
        import games.ops.telegram.models  # noqa: F401
        import games.instagram.models  # noqa: F401
        import games.social.models  # noqa: F401
        import games.ops.telegram.signals
        import games.site.ui.profile.feedback  # noqa: F401
        import games.puzzles.core.norm_matcher
        from health_check.plugins import plugin_dir
        from games.ops.health.checks import MediaStorageHealthCheck
        plugin_dir.register(MediaStorageHealthCheck)
