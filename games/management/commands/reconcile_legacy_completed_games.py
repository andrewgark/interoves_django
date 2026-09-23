import json
import re
import statistics
import time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from games.analytics import reconcile_legacy_completed_games_for_actor
from games.models import ChainTaskState


SUPPORTED_TASK_TYPES = (
    'raddle', 'replacements_lines', 'alphabetty', 'word_salad',
)
SUPPORTED_GAME_IDS = ('ladder', 'alphabetty', 'salad', 'replacements')


def _actor_queryset(kind):
    base = ChainTaskState.objects.filter(
        team__isnull=True,
        replay_slot__isnull=True,
        task__task_type__in=SUPPORTED_TASK_TYPES,
        game_id__in=SUPPORTED_GAME_IDS,
    )
    if kind == 'user':
        return (
            base.filter(user__isnull=False, anon_key__isnull=True)
            .values_list('user_id', flat=True)
            .distinct()
            .order_by('user_id')
        )
    return (
        base.filter(user__isnull=True, anon_key__isnull=False)
        .values_list('anon_key', flat=True)
        .distinct()
        .order_by('anon_key')
    )


class Command(BaseCommand):
    help = (
        'Reconcile legacy personal/anonymous completion history into '
        'PlayerCompletedGame, or audit it without writes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--audit', action='store_true', help='Read-only completeness audit.')
        parser.add_argument('--dry-run', action='store_true', help='Scan and report without writes.')
        parser.add_argument('--limit', type=int, default=None, help='Maximum actors processed in this run.')
        parser.add_argument('--batch-size', type=int, default=200, help='Actor iterator chunk size.')
        parser.add_argument(
            '--checkpoint-file',
            default=None,
            help='Private JSON checkpoint file used for resumable runs.',
        )
        parser.add_argument(
            '--retry-failed', action='store_true',
            help='Retry actor cursors recorded as failed in the checkpoint.',
        )
        parser.add_argument(
            '--profile', action='store_true',
            help='Collect privacy-safe phase timings and slow-actor aggregates.',
        )
        parser.add_argument(
            '--profile-sql', action='store_true',
            help='Capture per-actor SQL counts/fingerprints; use only for small limits.',
        )

    def _load_checkpoint(self, path):
        if not path:
            return {'phase': 'user', 'after_user_id': None, 'after_anon_key': None,
                    'failed_users': [], 'failed_anons': []}
        try:
            value = json.loads(Path(path).read_text())
        except FileNotFoundError:
            return {'phase': 'user', 'after_user_id': None, 'after_anon_key': None,
                    'failed_users': [], 'failed_anons': []}
        except (OSError, ValueError) as exc:
            raise CommandError('cannot read checkpoint file') from exc
        if not isinstance(value, dict):
            raise CommandError('checkpoint must contain a JSON object')
        value.setdefault('phase', 'user')
        value.setdefault('after_user_id', None)
        value.setdefault('after_anon_key', None)
        value.setdefault('failed_users', [])
        value.setdefault('failed_anons', [])
        return value

    def _save_checkpoint(self, path, checkpoint):
        if not path:
            return
        target = Path(path)
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_text(json.dumps(checkpoint, sort_keys=True))
        temporary.replace(target)

    def _summary(self, stats, elapsed):
        avg = stats['actor_time_total'] / stats['actors_completed'] if stats['actors_completed'] else 0.0
        median = statistics.median(stats['actor_times']) if stats['actor_times'] else 0.0
        self.stdout.write(
            'actors_scanned={actors_scanned} actors_completed={actors_completed} '
            'actors_failed={actors_failed} chain_states_scanned={chain_states_scanned} '
            'completion_candidates={completion_candidates} '
            'legacy_complete_instances={legacy_complete_instances} '
            'incomplete_candidates={incomplete_candidates} existing_pcg={existing_records} '
            'created_pcg={created_records} missing_pcg={missing_records} '
            'ambiguous_pcg={ambiguous_records} elapsed_seconds={elapsed:.3f} '
            'actor_avg_ms={avg:.1f} actor_median_ms={median:.1f} actor_max_ms={max_ms:.1f}'.format(
                **stats,
                elapsed=elapsed,
                avg=avg * 1000.0,
                median=median * 1000.0,
                max_ms=max(stats['actor_times'], default=0.0) * 1000.0,
            )
        )
        if stats['profile']:
            self.stdout.write(
                'profile_phase_ms legacy_completion_candidates={legacy:.1f} '
                'task_group_complete={group:.1f} player_completed_game_exists={pcg:.1f} '
                'activation_audit_logic={activation:.1f}'.format(
                    legacy=stats['timings'].get('legacy_completion_candidates_ms', 0.0),
                    group=stats['timings'].get('task_group_complete_ms', 0.0),
                    pcg=stats['timings'].get('player_completed_game_exists_ms', 0.0),
                    activation=stats['timings'].get('activation_audit_logic_ms', 0.0),
                )
            )
            if stats['actor_reports']:
                self.stdout.write('slow_actors_aggregate_top=%s' % ';'.join(
                    'elapsed_ms=%.1f states=%d candidates=%d existing=%d missing=%d' % report
                    for report in sorted(
                        stats['actor_reports'], reverse=True, key=lambda row: row[0],
                    )[:10]
                ))
        if stats['profile_sql']:
            avg_queries = (
                stats['query_total'] / stats['actors_completed']
                if stats['actors_completed'] else 0.0
            )
            self.stdout.write(
                'profile_sql total_queries=%d avg_queries_per_actor=%.1f max_queries_per_actor=%d '
                'query_fingerprints=%s' % (
                    stats['query_total'], avg_queries, stats['query_max'],
                    ';'.join('%s:%d' % item for item in stats['query_fingerprints'].most_common(12)),
                )
            )

    def _progress(self, stats, started, last_progress):
        now = time.monotonic()
        if stats['actors_scanned'] == 0:
            return last_progress
        if stats['actors_scanned'] - last_progress[0] < 10 and now - last_progress[1] < 30:
            return last_progress
        completed = stats['actors_completed']
        average = stats['actor_time_total'] / completed if completed else 0.0
        interval_elapsed = now - last_progress[1]
        interval_actors = stats['actors_scanned'] - last_progress[0]
        interval_ms = interval_elapsed * 1000.0 / interval_actors if interval_actors else 0.0
        total = stats.get('total_actors')
        remaining = max(total - stats['actors_scanned'], 0) if total is not None else None
        eta = remaining * average if remaining is not None and average else None
        self.stdout.write(
            'progress actors_processed=%d actors_failed=%d states=%d candidates=%d '
            'existing=%d missing=%d created=%d elapsed=%.1fs average_actor_ms=%.1f '
            'last_interval_actor_ms=%.1f estimated_remaining=%s estimated_remaining_seconds=%s' % (
                stats['actors_scanned'], stats['actors_failed'], stats['chain_states_scanned'],
                stats['completion_candidates'], stats['existing_records'], stats['missing_records'],
                stats['created_records'], now - started, average * 1000.0, interval_ms,
                remaining if remaining is not None else 'unknown',
                '%.1f' % eta if eta is not None else 'unknown',
            )
        )
        return (stats['actors_scanned'], now)

    @staticmethod
    def _query_fingerprint(sql):
        return re.sub(r"'[^']*'|\b\d+\b", '?', sql.replace('\n', ' '))[:180]

    def _process_actor(self, *, kind, value, dry_run, audit, cutoff, profile=False, profile_sql=False):
        User = get_user_model()
        if kind == 'user':
            user = User.objects.only('pk').filter(pk=value).first()
            if user is None:
                raise RuntimeError('user no longer exists')
            kwargs = {'user': user, 'anon_key': None}
        else:
            kwargs = {'user': None, 'anon_key': value}
        timings = {} if profile else None
        context = CaptureQueriesContext(connection) if profile_sql else None
        if context is None:
            with transaction.atomic():
                result = reconcile_legacy_completed_games_for_actor(
                    **kwargs,
                    dry_run=dry_run or audit,
                    activation_cutoff=cutoff,
                    history_cutoff=None if audit else cutoff,
                    _timings=timings,
                )
            return result, timings, 0, []
        with context:
            with transaction.atomic():
                result = reconcile_legacy_completed_games_for_actor(
                    **kwargs,
                    dry_run=dry_run or audit,
                    activation_cutoff=cutoff,
                    history_cutoff=None if audit else cutoff,
                    _timings=timings,
                )
        return result, timings, len(context), [self._query_fingerprint(q['sql']) for q in context.captured_queries]

    def handle(self, *args, **options):
        if options['audit'] and options['dry_run']:
            raise CommandError('--audit and --dry-run are mutually exclusive')
        if options['batch_size'] <= 0:
            raise CommandError('--batch-size must be positive')
        if options['limit'] is not None and options['limit'] <= 0:
            raise CommandError('--limit must be positive')
        if options['profile_sql'] and not options['profile']:
            options['profile'] = True

        checkpoint_path = options['checkpoint_file']
        checkpoint = self._load_checkpoint(checkpoint_path)
        retry_failed = options['retry_failed']
        dry_run = options['dry_run']
        audit = options['audit']
        cutoff = timezone.now()
        started = time.monotonic()
        stats = {
            'actors_scanned': 0,
            'actors_completed': 0,
            'actors_failed': 0,
            'chain_states_scanned': 0,
            'completion_candidates': 0,
            'legacy_complete_instances': 0,
            'incomplete_candidates': 0,
            'existing_records': 0,
            'created_records': 0,
            'missing_records': 0,
            'ambiguous_records': 0,
            'actor_time_total': 0.0,
            'actor_times': [],
            'timings': {},
            'actor_reports': [],
            'profile': options['profile'],
            'profile_sql': options['profile_sql'],
            'query_total': 0,
            'query_max': 0,
            'query_fingerprints': None,
        }
        from collections import Counter
        stats['query_fingerprints'] = Counter()
        total_users = _actor_queryset('user').count()
        total_anons = _actor_queryset('anon').count()
        stats['total_actors'] = total_users + total_anons
        remaining = options['limit']
        last_progress = (0, started)

        phases = ('user', 'anon')
        for kind in phases:
            if checkpoint['phase'] == 'anon' and kind == 'user':
                continue
            after = checkpoint['after_user_id'] if kind == 'user' else checkpoint['after_anon_key']
            queryset = _actor_queryset(kind)
            if after is not None:
                field = 'user_id' if kind == 'user' else 'anon_key'
                queryset = queryset.filter(**{'{}__gt'.format(field): after})

            failed_values = checkpoint['failed_users'] if kind == 'user' else checkpoint['failed_anons']
            if retry_failed:
                for value in list(failed_values):
                    actor_started = time.monotonic()
                    stats['actors_scanned'] += 1
                    try:
                        result, timings, query_count, fingerprints = self._process_actor(
                            kind=kind, value=value, dry_run=dry_run, audit=audit,
                            cutoff=cutoff, profile=options['profile'], profile_sql=options['profile_sql'],
                        )
                    except Exception:
                        stats['actors_failed'] += 1
                        last_progress = self._progress(stats, started, last_progress)
                        continue
                    stats['actors_completed'] += 1
                    if timings:
                        for key, value_ms in timings.items():
                            stats['timings'][key] = stats['timings'].get(key, 0.0) + value_ms
                    elapsed = time.monotonic() - actor_started
                    stats['actor_time_total'] += elapsed
                    stats['actor_times'].append(elapsed)
                    stats['actor_reports'].append((elapsed * 1000.0, result['chain_states_scanned'], result['completion_candidates'], result['existing_records'], result['missing_records']))
                    stats['query_total'] += query_count
                    stats['query_max'] = max(stats['query_max'], query_count)
                    stats['query_fingerprints'].update(fingerprints)
                    for key in (
                        'chain_states_scanned', 'completion_candidates',
                        'legacy_complete_instances',
                        'incomplete_candidates', 'existing_records',
                        'created_records', 'missing_records', 'ambiguous_records',
                    ):
                        stats[key] += result[key]
                    failed_values.remove(value)
                    last_progress = self._progress(stats, started, last_progress)
                self._save_checkpoint(checkpoint_path, checkpoint)

            for value in queryset.iterator(chunk_size=options['batch_size']):
                if remaining is not None and remaining <= 0:
                    break
                remaining = remaining - 1 if remaining is not None else None
                actor_started = time.monotonic()
                stats['actors_scanned'] += 1
                try:
                    result, timings, query_count, fingerprints = self._process_actor(
                        kind=kind, value=value, dry_run=dry_run, audit=audit,
                        cutoff=cutoff, profile=options['profile'], profile_sql=options['profile_sql'],
                    )
                except Exception:
                    stats['actors_failed'] += 1
                    if value not in failed_values:
                        failed_values.append(value)
                    if kind == 'user':
                        checkpoint['after_user_id'] = value
                    else:
                        checkpoint['after_anon_key'] = value
                    self._save_checkpoint(checkpoint_path, checkpoint)
                    last_progress = self._progress(stats, started, last_progress)
                    continue
                stats['actors_completed'] += 1
                if timings:
                    for key, value_ms in timings.items():
                        stats['timings'][key] = stats['timings'].get(key, 0.0) + value_ms
                elapsed = time.monotonic() - actor_started
                stats['actor_time_total'] += elapsed
                stats['actor_times'].append(elapsed)
                stats['actor_reports'].append((elapsed * 1000.0, result['chain_states_scanned'], result['completion_candidates'], result['existing_records'], result['missing_records']))
                stats['query_total'] += query_count
                stats['query_max'] = max(stats['query_max'], query_count)
                stats['query_fingerprints'].update(fingerprints)
                for key in (
                    'chain_states_scanned', 'completion_candidates',
                    'legacy_complete_instances',
                    'incomplete_candidates', 'existing_records',
                    'created_records', 'missing_records', 'ambiguous_records',
                ):
                    stats[key] += result[key]
                if kind == 'user':
                    checkpoint['after_user_id'] = value
                else:
                    checkpoint['after_anon_key'] = value
                self._save_checkpoint(checkpoint_path, checkpoint)
                last_progress = self._progress(stats, started, last_progress)

            if remaining is not None and remaining <= 0:
                break
            checkpoint['phase'] = 'anon' if kind == 'user' else 'done'
            self._save_checkpoint(checkpoint_path, checkpoint)

        self._summary(stats, time.monotonic() - started)
        if audit and (stats['missing_records'] or stats['ambiguous_records'] or stats['actors_failed']):
            raise CommandError('legacy completion audit failed')
