import socket

from django.core.management.base import BaseCommand

from games.anonymous_merge import run_anonymous_merge_queue


class Command(BaseCommand):
    help = 'Process durable anonymous-profile merge jobs (for the EB minute cron).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1)

    def handle(self, *args, **options):
        processed = run_anonymous_merge_queue(
            limit=max(0, options['limit']),
            worker='cron:{}'.format(socket.gethostname()),
        )
        self.stdout.write('Anonymous merge jobs processed: {}.'.format(processed))
