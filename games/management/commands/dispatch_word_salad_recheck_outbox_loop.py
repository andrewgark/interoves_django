import signal
import time

from django.core.management.base import BaseCommand, CommandError

from games.runtime import heavy_processing_allowed
from games.word_salad_outbox import dispatch_word_salad_recheck_outbox


class Command(BaseCommand):
    help = 'Continuously dispatch Word Salad DB outbox rows from a worker runtime.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=25)
        parser.add_argument('--interval', type=float, default=15.0)

    def handle(self, *args, **options):
        if not heavy_processing_allowed():
            raise CommandError('Word Salad heavy processing is disabled for the web runtime role')
        stopped = False

        def stop(*_args):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        limit = max(1, options['limit'])
        interval = max(0.1, options['interval'])
        while not stopped:
            result = dispatch_word_salad_recheck_outbox(limit=limit)
            self.stdout.write(
                'Word Salad outbox loop sent={sent} failed={failed}.'.format(**result),
                ending='\n',
            )
            self.stdout.flush()
            if not stopped:
                time.sleep(interval)
