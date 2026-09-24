from django.core.management.base import BaseCommand

from games.word_salad_outbox import reconcile_word_salad_recheck_outbox


class Command(BaseCommand):
    help = 'Audit Word Salad recheck outbox rows; use --apply for safe repairs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Repair missing/stale/terminal outbox rows (default is read-only).',
        )

    def handle(self, *args, **options):
        findings = reconcile_word_salad_recheck_outbox(apply=options['apply'])
        mode = 'Applied' if options['apply'] else 'Found'
        self.stdout.write('{} {} outbox reconciliation findings.'.format(mode, len(findings)))
        for finding in findings:
            suffix = ' repaired' if finding.get('repaired') else ''
            self.stdout.write(' - {}{}: {}'.format(finding['kind'], suffix, finding))
