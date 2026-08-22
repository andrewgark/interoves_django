from django.apps import AppConfig


class GamesConfig(AppConfig):
    name = 'games'

    def ready(self):
        import games.tribute_checks  # noqa: F401
        import games.signals
        import games.telegram.models  # noqa: F401
        import games.instagram.models  # noqa: F401
        import games.social.models  # noqa: F401
        import games.telegram.signals
        import games.feedback  # noqa: F401
        import games.matcher.norm_matcher
