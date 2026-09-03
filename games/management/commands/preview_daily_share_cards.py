from django.core.management.base import BaseCommand, CommandError

from games.telegram.daily_share_preview import preview_daily_share_cards


class Command(BaseCommand):
    help = (
        'Render daily-game share cards with the production JS renderer and '
        'send them to the existing Telegram admin chat. Does not publish '
        'to the public channel or deploy production.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-social',
            action='store_true',
            help='Do not also send the current ladder/salad social teasers.',
        )

    def handle(self, *args, **options):
        ok, message = preview_daily_share_cards(
            include_social=not options.get('skip_social'),
        )
        if ok:
            self.stdout.write(self.style.SUCCESS(message))
            return
        raise CommandError(message)
