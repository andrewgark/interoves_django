import json

from django.core.management.base import BaseCommand, CommandError

from games.management.commands._player_analytics_unique_indexes import (
    collect_preflight,
)


class Command(BaseCommand):
    help = (
        'Run full-key, read-only preflight checks for the nine player analytics '
        'unique indexes. Output never includes actor values.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')
        parser.add_argument(
            '--format',
            choices=('human', 'json'),
            default='human',
        )

    def _write_human(self, report):
        self.stdout.write('Player analytics unique-index preflight')
        self.stdout.write('status: {}'.format(report['status']))
        self.stdout.write('database vendor: {}'.format(report['database_vendor']))
        self.stdout.write('server version: {}'.format(report['server_version']))
        self.stdout.write('duplicate groups:')
        for name, count in report['duplicate_groups'].items():
            self.stdout.write('  {}: {}'.format(name, count))
        self.stdout.write('identity XOR violations:')
        for name, count in report['identity_violations'].items():
            self.stdout.write('  {}: {}'.format(name, count))
        self.stdout.write('tables:')
        for name, details in report['tables'].items():
            self.stdout.write('  {}: {}'.format(name, json.dumps(
                details, sort_keys=True, ensure_ascii=True,
            )))
        self.stdout.write('future indexes:')
        for name, details in report['expected_indexes'].items():
            self.stdout.write('  {}: {}'.format(name, json.dumps(
                details, sort_keys=True, ensure_ascii=True,
            )))
        self.stdout.write('current SHOW INDEX equivalent:')
        for table, indexes in report['indexes'].items():
            self.stdout.write('  {}: {}'.format(table, json.dumps(
                indexes, sort_keys=True, ensure_ascii=True,
            )))
        for label in (
            'free_storage_space',
            'transactions',
            'metadata_locks',
            'online_ddl',
        ):
            self.stdout.write('{}: {}'.format(label, json.dumps(
                report[label], sort_keys=True, ensure_ascii=True,
            )))

    def handle(self, *args, **options):
        report = collect_preflight(using=options['database'])
        if options['format'] == 'json':
            self.stdout.write(json.dumps(report, sort_keys=True, ensure_ascii=True))
        else:
            self._write_human(report)
        if report['status'] != 'PASS_DATA_CHECKS':
            raise CommandError(
                'preflight blocked by duplicate groups or identity violations'
            )
