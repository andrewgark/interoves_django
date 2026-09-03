from django.apps import AppConfig


class GamesConfig(AppConfig):
    name = 'games'

    def ready(self):
        # Import registers Django's login/logout signal handlers. The startup
        # event contains fingerprints only, never either secret itself.
        from games import auth_observability

        auth_observability.log_startup_auth_configuration()
        import games.tribute_checks  # noqa: F401
        import games.signals
        import games.telegram.models  # noqa: F401
        import games.instagram.models  # noqa: F401
        import games.social.models  # noqa: F401
        import games.telegram.signals
        import games.feedback  # noqa: F401
        import games.matcher.norm_matcher
