import socket

from django.core.management.base import BaseCommand, CommandError

from games.runtime import heavy_processing_allowed
from games.word_salad_recheck import process_word_salad_rechecks


class Command(BaseCommand):
    help = 'Process durable Word Salad actor recheck jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1)

    def handle(self, *args, **options):
        if not heavy_processing_allowed():
            raise CommandError('Word Salad heavy processing is disabled for the web runtime role')
        count = process_word_salad_rechecks(
            limit=max(0, options['limit']),
            worker='cron:{}'.format(socket.gethostname()),
        )
        self.stdout.write('Word Salad recheck jobs progressed: {}.'.format(count))
