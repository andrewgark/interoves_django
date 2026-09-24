from django.core.management.base import BaseCommand, CommandError

from games.runtime import heavy_processing_allowed
from games.word_salad_outbox import dispatch_word_salad_recheck_outbox


class Command(BaseCommand):
    help = 'Dispatch Word Salad DB outbox rows to the configured transport.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)

    def handle(self, *args, **options):
        if not heavy_processing_allowed():
            raise CommandError('Word Salad heavy processing is disabled for the web runtime role')
        result = dispatch_word_salad_recheck_outbox(limit=max(0, options['limit']))
        self.stdout.write('Word Salad outbox sent={sent} failed={failed}.'.format(**result))
