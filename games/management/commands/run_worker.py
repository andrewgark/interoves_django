"""Run one worker through the runtime-neutral SQS polling adapter."""

import os

from django.core.management.base import BaseCommand, CommandError

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

        import boto3

        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION')
            or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        while True:
            result = poll_once(
                worker_name=config.worker.name,
                client=client,
                queue_url=queue_url,
                wait_seconds=options['wait_seconds'],
                visibility_timeout=options.get('visibility_timeout'),
            )
            self.stdout.write(str(result))
            if options['once']:
                return
