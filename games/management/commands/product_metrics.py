import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.product_metrics import (
    TRUSTED_IDENTITY_CUTOVER,
    TRUSTED_IDENTITY_CUTOVER_SHA,
    build_product_metrics_report,
)


class Command(BaseCommand):
    help = 'Print canonical Stage 2A product metrics for a datetime window.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since',
            help='Inclusive ISO datetime. Default: trusted identity cutover.',
        )
        parser.add_argument(
            '--until',
            help='Exclusive ISO datetime. Default: now.',
        )
        parser.add_argument('--game-type', dest='game_kind', help='Filter by stored game_kind.')
        parser.add_argument('--placement', help='Filter by game_instance_id.')
        parser.add_argument(
            '--format',
            choices=('text', 'json'),
            default='text',
            dest='output_format',
        )

    def _parse_bound(self, raw, label):
        if not raw:
            return None
        value = parse_datetime(raw)
        if value is None:
            raise CommandError('{} must be an ISO datetime'.format(label))
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def handle(self, *args, **options):
        since = self._parse_bound(options.get('since'), '--since')
        until = self._parse_bound(options.get('until'), '--until')
        try:
            report = build_product_metrics_report(
                since,
                until,
                game_kind=options.get('game_kind'),
                placement=options.get('placement'),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = report.to_dict()
        if options['output_format'] == 'json':
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return

        if report.legacy_contaminated:
            self.stdout.write(
                'WARNING: window starts before trusted identity cutover '
                '({} / {}); pre-cutover identity is not trusted.'.format(
                    TRUSTED_IDENTITY_CUTOVER_SHA,
                    TRUSTED_IDENTITY_CUTOVER.isoformat(),
                )
            )
        self._write_text(payload)

    def _write_text(self, payload):
        overview = payload['overview']
        engagement = payload['engagement']
        core = payload['core']
        self.stdout.write('Product metrics')
        self.stdout.write('since: {}'.format(payload['since']))
        self.stdout.write('until: {}'.format(payload['until']))
        self.stdout.write('timezone: {}'.format(payload['timezone']))
        self.stdout.write('trusted_cutover: {} ({})'.format(
            payload['trusted_cutover'], payload['trusted_cutover_sha'],
        ))
        self.stdout.write('legacy_contaminated: {}'.format(
            'yes' if payload['legacy_contaminated'] else 'no',
        ))
        if payload.get('game_kind'):
            self.stdout.write('game_kind: {}'.format(payload['game_kind']))
        if payload.get('placement'):
            self.stdout.write('placement: {}'.format(payload['placement']))
        self.stdout.write('')
        self.stdout.write('Overview')
        self.stdout.write('  players: {}'.format(overview['players']))
        self.stdout.write('  new_players: {}'.format(overview['new_players']))
        self.stdout.write('  starts: {}'.format(overview['starts']))
        self.stdout.write('  completions: {}'.format(overview['completions']))
        self.stdout.write('  completion_rate: {}'.format(
            self._fmt_rate(overview['completion_rate']),
        ))
        self.stdout.write('  registered_players: {}'.format(overview['registered_players']))
        self.stdout.write('  anonymous_players: {}'.format(overview['anonymous_players']))
        self.stdout.write('  team_players: {}'.format(overview['team_players']))
        self.stdout.write('')
        self.stdout.write('Engagement')
        self.stdout.write('  dau_average: {}'.format(engagement['dau_average']))
        self.stdout.write('  dau_complete_days: {}'.format(engagement['dau_complete_days']))
        self.stdout.write('  wau: {}'.format(engagement['wau']))
        self.stdout.write('  mau: {}'.format(engagement['mau']))
        self.stdout.write('  active_days_distribution: {}'.format(
            engagement['active_days_distribution'],
        ))
        self.stdout.write('')
        self.stdout.write('Retention')
        if not payload['retention']:
            self.stdout.write('  (none)')
        for row in payload['retention']:
            self.stdout.write(
                '  {} n={} exact_d1={} exact_d7={} rolling_d7={}'.format(
                    row['cohort_date'],
                    row['cohort_size'],
                    self._fmt_rate(row['exact_d1']),
                    self._fmt_rate(row['exact_d7']),
                    self._fmt_rate(row['rolling_d7']),
                )
            )
        self.stdout.write('')
        self.stdout.write('Core ({}d to until)'.format(core['window_days']))
        self.stdout.write('  active_days_7_plus: {}'.format(core['active_days_7_plus']))
        self.stdout.write('  completions_10_plus: {}'.format(core['completions_10_plus']))
        self.stdout.write('  completions_20_plus: {}'.format(core['completions_20_plus']))
        self.stdout.write('  active_weeks_3_plus: {}'.format(core['active_weeks_3_plus']))

    def _fmt_rate(self, payload):
        status = payload.get('status')
        if payload.get('value') is None:
            return status or 'empty'
        return '{:.4f} ({})'.format(payload['value'], status)
