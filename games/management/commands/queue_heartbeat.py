import socket

from django.core.management.base import BaseCommand

from games.queue_heartbeat import finish_record, start


class Command(BaseCommand):
    help = 'Record a durable cron worker heartbeat.'

    def add_arguments(self, parser):
        parser.add_argument('--queue', required=True)
        parser.add_argument('--state', choices=('start', 'success', 'failed'), required=True)
        parser.add_argument('--error', default='')
        parser.add_argument('--processed', type=int, default=None)

    def handle(self, *args, **options):
        queue = options['queue']
        if options['state'] == 'start':
            start(queue, worker='cron:{}'.format(socket.gethostname()))
        else:
            finish_record(
                queue,
                success=options['state'] == 'success',
                error=options['error'],
                processed_count=options['processed'],
            )
