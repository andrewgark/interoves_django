"""Re-parse saved Цензурки articles at a chosen Wikipedia revision.

By default, only parser fixes reach players: the revision stays. A task whose
body text comes out the same is not touched (its open games do not reload).
"""

from __future__ import annotations

import difflib
import json
import time

import requests
from django.core.management.base import BaseCommand
from games.console.games.censorly import CensorlySupportError, refetch_task_text
from games.models import Task
from games.puzzles.censorly.ids import CENSORLY_TASK_TYPE
from games.puzzles.censorly.text.tokenize import FORMULA_START


def _first_difference(old: str, new: str, width: int = 90) -> str:
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            return f'{old[max(0, i1 - 30):i2 + 30][:width]!r} -> {new[max(0, j1 - 30):j2 + 30][:width]!r}'
    return ''


class Command(BaseCommand):
    help = (
        'Re-parse Censorly articles at their saved revision (or the latest with '
        '--latest-revision) and save the ones whose text changed.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report changes, save nothing')
        parser.add_argument(
            '--latest-revision', action='store_true',
            help='Parse the current article revision instead of the saved one',
        )
        parser.add_argument('--task-ids', default='', help='Comma-separated Task ids (default: all)')
        parser.add_argument('--start-id', type=int, default=0, help='Resume from this Task id')
        parser.add_argument('--limit', type=int, default=0, help='Max tasks to process (0 = all)')
        parser.add_argument('--sleep', type=float, default=1.0, help='Seconds between Wikipedia requests')
        parser.add_argument('--report', default='', help='Write a JSON lines report to this path')

    def handle(self, *args, **options):
        dry = options['dry_run']
        pause = max(0.0, float(options['sleep']))
        qs = Task.objects.filter(task_type=CENSORLY_TASK_TYPE, is_removed=False).order_by('id')
        if options['task_ids']:
            qs = qs.filter(id__in=[int(item) for item in options['task_ids'].split(',') if item.strip()])
        if options['start_id']:
            qs = qs.filter(id__gte=options['start_id'])
        if options['limit']:
            qs = qs[:options['limit']]
        report = open(options['report'], 'a', encoding='utf-8') if options['report'] else None
        session = requests.Session()
        counts = {'seen': 0, 'changed': 0, 'saved': 0, 'unchanged': 0, 'errors': 0}
        try:
            for task in qs.iterator():
                counts['seen'] += 1
                result, error = self._refetch(
                    task, dry=dry, session=session, pause=pause, latest=options['latest_revision'],
                )
                if error:
                    counts['errors'] += 1
                    self.stderr.write(f'#{task.pk}: {error}')
                    row = {'task_id': task.pk, 'error': error}
                elif not result['changed']:
                    counts['unchanged'] += 1
                    row = None
                else:
                    counts['changed'] += 1
                    counts['saved'] += int(result['updated'])
                    old_text, new_text = result['old_body_text'], result['body_text']
                    row = {
                        'task_id': task.pk,
                        'title': result['wiki_title'],
                        'tokens': [result['old_token_count'], result['token_count']],
                        'formulas': [old_text.count(FORMULA_START), new_text.count(FORMULA_START)],
                        'chars': [len(old_text), len(new_text)],
                        'saved': result['updated'],
                        'first_difference': _first_difference(old_text, new_text),
                    }
                    self.stdout.write(
                        f'#{task.pk} {row["title"]}: tokens {row["tokens"][0]} -> {row["tokens"][1]}, '
                        f'formulas {row["formulas"][0]} -> {row["formulas"][1]}'
                        f'{"" if dry else " saved" if result["updated"] else " NOT saved"}',
                    )
                if report is not None and row is not None:
                    report.write(json.dumps(row, ensure_ascii=False) + '\n')
                    report.flush()
                time.sleep(pause)
        finally:
            if report is not None:
                report.close()
        self.stdout.write(self.style.SUCCESS(
            ' '.join(f'{key}={value}' for key, value in counts.items()) + f' dry_run={dry}',
        ))

    def _refetch(self, task, *, dry, session, pause, latest=False):
        """(result, error) for one task, retrying Wikipedia failures with backoff."""
        last_error = ''
        for attempt in range(4):
            try:
                return refetch_task_text(
                    task, same_revision=not latest, only_if_changed=True, dry_run=dry, session=session,
                ), ''
            except CensorlySupportError as exc:
                last_error = str(exc)
                # A rate limit or a network hiccup is worth another try; a
                # missing revision or an empty title is not.
                if 'Не удалось загрузить' not in last_error and 'Wikipedia API' not in last_error:
                    return None, last_error
            time.sleep(max(pause, 1.0) * (5 ** attempt))
        return None, last_error
