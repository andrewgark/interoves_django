from django.apps import AppConfig


class GamesConfig(AppConfig):
    name = 'games'

    def ready(self):
        import games.signals
        import games.matcher.norm_matcher

