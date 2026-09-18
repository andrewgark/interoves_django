import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from games.management.commands.audit_player_completed_games import Command as AuditCommand
from games.models import PlayerCompletedGame


REPAIR_COHORT = frozenset({
    5910, 6738, 7491, 7492, 8564, 8995, 9001, 9023, 9428,
    9712, 11581, 15779, 16581, 16600, 16830, 16850, 17262,
    19417, 19971, 20327, 20330, 20340, 20979, 22284, 22735,
})

CURRENT_INCOMPLETE_COHORT = frozenset({
    67, 103, 417, 1350, 1353, 2004, 2008, 2208, 2216, 2218,
    2222, 2232, 2239, 2246, 2250, 2251, 2255, 2258, 2527, 2641,
    2990, 3175, 3264, 3554, 3577, 4236, 4338, 4340, 4592, 4595,
    5115, 5448, 6022, 6025, 6026, 6031, 7526, 7716, 9571, 9631,
    9633, 9634, 13730, 14915, 19905, 21019, 21900,
})


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


class Command(BaseCommand):
    help = 'Safely dry-run or repair an explicit forensic PCG cohort.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--dry-run', action='store_true')
        mode.add_argument('--apply', action='store_true')
        parser.add_argument(
            '--ids', required=True,
            help='Comma-separated explicit PlayerCompletedGame IDs; no implicit scope is allowed.',
        )
        parser.add_argument(
            '--current-incomplete-only', action='store_true',
            help='Use the frozen current-incomplete cohort; ignore historical bug classification.',
        )
        parser.add_argument(
            '--export',
            help='Write a local JSON backup/evidence export before completing the command.',
        )

    def handle(self, *args, **options):
        ids = self._parse_ids(options['ids'])
        requested = len(ids)
        current_only = options['current_incomplete_only']
        allowed_cohort = CURRENT_INCOMPLETE_COHORT if current_only else REPAIR_COHORT
        unknown = sorted(set(ids) - allowed_cohort)
        if unknown:
            raise CommandError(
                'IDs are not in the selected frozen repair cohort: {}'.format(unknown)
            )
        if options['apply'] and not options.get('export'):
            raise CommandError('--apply requires --export so the pre-write backup is explicit.')

        records = list(
            PlayerCompletedGame.objects.select_related('game', 'task_group')
            .filter(pk__in=ids)
            .order_by('pk')
        )
        by_id = {record.pk: record for record in records}
        missing = [pk for pk in ids if pk not in by_id]
        validations = self._validate(records)
        report = {
            'requested': requested,
            'still_confirmed_invalid': [],
            'skipped_now_valid': [],
            'skipped_ambiguous': [],
            'skipped_not_task_group': [],
            'missing': missing,
            'eligible': [],
            'records_changed': 0,
            'items': [],
        }
        for pk in ids:
            record = by_id.get(pk)
            if record is None:
                continue
            item = validations.get(pk)
            if not record.game.is_tournament:
                reason = 'completion is outside the structural task-group universe'
                report['skipped_not_task_group'].append(pk)
            elif item is None:
                reason = 'forensic validation did not produce a result'
                report['skipped_ambiguous'].append(pk)
            elif current_only and item['missing_tasks_current']:
                report['eligible'].append(pk)
                reason = 'current canonical task-group state is incomplete'
            elif not current_only and item['classification'] == 'CONFIRMED_INVALID':
                report['still_confirmed_invalid'].append(pk)
                report['eligible'].append(pk)
                reason = item['reason']
            elif item['classification'] == 'VALID':
                report['skipped_now_valid'].append(pk)
                reason = item['reason']
            else:
                report['skipped_ambiguous'].append(pk)
                reason = item['reason']
            report['items'].append({
                'id': pk,
                'classification': item['classification'] if item else 'MISSING_VALIDATION',
                'eligible_for_repair': pk in report['eligible'],
                'reason': reason,
                'forensic': item,
            })

        if options.get('export'):
            report['current_incomplete_only'] = current_only
            self._export(options['export'], records, report)

        if options['apply']:
            report = self._apply_after_revalidation(ids, report, current_only=current_only)

        self.stdout.write(json.dumps({
            key: report[key]
            for key in (
                'requested', 'still_confirmed_invalid', 'skipped_now_valid',
                'skipped_ambiguous', 'skipped_not_task_group', 'missing',
                'eligible', 'records_changed',
            )
        }, ensure_ascii=False, sort_keys=True))
        if options['dry_run']:
            self.stdout.write('DRY_RUN records_changed=0')

    def _parse_ids(self, raw):
        try:
            ids = [int(value.strip()) for value in raw.split(',') if value.strip()]
        except ValueError as exc:
            raise CommandError('--ids must be a comma-separated list of integers') from exc
        if not ids or len(ids) != len(set(ids)):
            raise CommandError('--ids must contain at least one unique integer ID')
        return ids

    def _validate(self, records):
        if not records:
            return {}
        auditor = AuditCommand()
        snapshot = auditor._snapshot(records)
        return {record.pk: auditor._audit(record, snapshot) for record in records}

    def _export(self, raw_path, records, report):
        path = Path(raw_path)
        payload = {
            'model': 'games.PlayerCompletedGame',
            'frozen_repair_cohort': sorted(
                CURRENT_INCOMPLETE_COHORT if report.get('current_incomplete_only') else REPAIR_COHORT
            ),
            'records': [
                {
                    field.attname: _json_value(getattr(record, field.attname))
                    for field in record._meta.concrete_fields
                }
                for record in records
            ],
            'validation': report['items'],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n')

    def _apply_after_revalidation(self, ids, initial_report, *, current_only=False):
        with transaction.atomic():
            # Lock first, then rebuild the snapshot immediately before the
            # write.  The expensive scan is limited to the explicit cohort.
            current_records = list(
                PlayerCompletedGame.objects.select_for_update()
                .select_related('game', 'task_group')
                .filter(pk__in=ids)
                .order_by('pk')
            )
            current_by_id = {record.pk: record for record in current_records}
            current_validation = self._validate(current_records)
            eligible = [
                pk for pk in ids
                if pk in current_by_id
                and current_by_id[pk].game.is_tournament
                and (
                    current_validation.get(pk, {}).get('missing_tasks_current')
                    if current_only else
                    current_validation.get(pk, {}).get('classification') == 'CONFIRMED_INVALID'
                )
            ]
            deleted, _details = PlayerCompletedGame.objects.filter(pk__in=eligible).delete()
        initial_eligible = set(initial_report['eligible'])
        current_eligible = set(eligible)
        for pk in sorted(initial_eligible - current_eligible):
            initial_report['eligible'].remove(pk)
            if pk in initial_report['still_confirmed_invalid']:
                initial_report['still_confirmed_invalid'].remove(pk)
            item = current_validation.get(pk)
            if item is None:
                if pk not in initial_report['missing']:
                    initial_report['missing'].append(pk)
                continue
            if item['classification'] == 'VALID':
                initial_report['skipped_now_valid'].append(pk)
            else:
                initial_report['skipped_ambiguous'].append(pk)
        initial_report['eligible'] = eligible
        initial_report['still_confirmed_invalid'] = [] if current_only else eligible
        initial_report['records_changed'] = deleted
        return initial_report
