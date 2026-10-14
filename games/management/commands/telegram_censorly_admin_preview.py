from django.core.management.base import BaseCommand

from games.telegram.censorly_channel import preview_censorly_to_admin


class Command(BaseCommand):
    help = 'Render a Censorly teaser and send it to the admin chat only.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--number', type=int,
            help='Preview a specific daily number, including one not yet scheduled today.',
        )

    def handle(self, *args, **options):
        ok, message = preview_censorly_to_admin(number=options.get('number'))
        if ok:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stderr.write(message)
