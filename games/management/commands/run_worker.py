"""Run one worker through the runtime-neutral SQS polling adapter."""

import os
import signal

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from games.worker_config import load_worker_config
from games.worker_contract import WORKER_REGISTRY
from games.worker_polling import poll_once, queue_url_for


class Command(BaseCommand):
    help = 'Run a worker using the common SQS polling adapter (ECS/Green process).'

    def add_arguments(self, parser):
        parser.add_argument('--worker', required=True, choices=tuple(spec.name for spec in WORKER_REGISTRY.all()))
        parser.add_argument('--mode', default='ecs-fargate')
        parser.add_argument('--queue-url')
        parser.add_argument('--wait-seconds', type=int, default=20)
        parser.add_argument('--visibility-timeout', type=int)
        parser.add_argument('--once', action='store_true', help='Process one receive cycle and exit.')

    def handle(self, *args, **options):
        try:
            config = load_worker_config(worker_name=options['worker'], mode=options['mode'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        queue_url = (options.get('queue_url') or queue_url_for(config.worker)).strip()
        if not queue_url:
            raise CommandError(
                'queue URL is missing; pass --queue-url or set {}'.format(
                    config.worker.queue_url_env,
                )
            )
        if config.missing:
            raise CommandError(
                'worker configuration is incomplete; missing: {}'.format(
                    ', '.join(config.missing),
                )
            )

        visibility_timeout = options.get('visibility_timeout')
        if visibility_timeout is None and config.worker.name == 'identity':
            # Authenticated account merges are intentionally atomic and may
            # touch many related tables. Keep the SQS message invisible for
            # the full worker lease instead of redelivering it mid-transaction.
            visibility_timeout = 1800

        import boto3

        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION')
            or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        stopping = False

        def request_stop(signum, frame):
            nonlocal stopping
            stopping = True
            self.stdout.write('worker_stop_requested signal={}'.format(signum))

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        while True:
            if config.worker.name == 'recheck':
                try:
                    from games.word_salad_outbox import dispatch_recheck_outbox
                    close_old_connections()
                    dispatched = dispatch_recheck_outbox(limit=10)
                    self.stdout.write(
                        'recheck outbox sent={sent} failed={failed}.'.format(**dispatched),
                    )
                except Exception as exc:
                    self.stderr.write(
                        'recheck outbox dispatch failed: {}: {}'.format(
                            exc.__class__.__name__, exc,
                        ),
                    )
            result = poll_once(
                worker_name=config.worker.name,
                client=client,
                queue_url=queue_url,
                wait_seconds=options['wait_seconds'],
                visibility_timeout=visibility_timeout,
            )
            self.stdout.write(str(result))
            if options['once'] or stopping:
                if stopping:
                    self.stdout.write('worker_stopped=graceful')
                return
