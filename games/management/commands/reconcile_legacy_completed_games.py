import json
import statistics
import time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
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

    def _process_actor(self, *, kind, value, dry_run, audit, cutoff):
        User = get_user_model()
        if kind == 'user':
            user = User.objects.only('pk').filter(pk=value).first()
            if user is None:
                raise RuntimeError('user no longer exists')
            kwargs = {'user': user, 'anon_key': None}
        else:
            kwargs = {'user': None, 'anon_key': value}
        with transaction.atomic():
            return reconcile_legacy_completed_games_for_actor(
                **kwargs,
                dry_run=dry_run or audit,
                activation_cutoff=cutoff,
                history_cutoff=None if audit else cutoff,
            )

    def handle(self, *args, **options):
        if options['audit'] and options['dry_run']:
            raise CommandError('--audit and --dry-run are mutually exclusive')
        if options['batch_size'] <= 0:
            raise CommandError('--batch-size must be positive')
        if options['limit'] is not None and options['limit'] <= 0:
            raise CommandError('--limit must be positive')

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
        }
        remaining = options['limit']

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
                        result = self._process_actor(
                            kind=kind, value=value, dry_run=dry_run, audit=audit,
                            cutoff=cutoff,
                        )
                    except Exception:
                        stats['actors_failed'] += 1
                        continue
                    stats['actors_completed'] += 1
                    elapsed = time.monotonic() - actor_started
                    stats['actor_time_total'] += elapsed
                    stats['actor_times'].append(elapsed)
                    for key in (
                        'chain_states_scanned', 'completion_candidates',
                        'legacy_complete_instances',
                        'incomplete_candidates', 'existing_records',
                        'created_records', 'missing_records', 'ambiguous_records',
                    ):
                        stats[key] += result[key]
                    failed_values.remove(value)
                self._save_checkpoint(checkpoint_path, checkpoint)

            for value in queryset.iterator(chunk_size=options['batch_size']):
                if remaining is not None and remaining <= 0:
                    break
                remaining = remaining - 1 if remaining is not None else None
                actor_started = time.monotonic()
                stats['actors_scanned'] += 1
                try:
                    result = self._process_actor(
                        kind=kind, value=value, dry_run=dry_run, audit=audit,
                        cutoff=cutoff,
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
                    continue
                stats['actors_completed'] += 1
                elapsed = time.monotonic() - actor_started
                stats['actor_time_total'] += elapsed
                stats['actor_times'].append(elapsed)
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

            if remaining is not None and remaining <= 0:
                break
            checkpoint['phase'] = 'anon' if kind == 'user' else 'done'
            self._save_checkpoint(checkpoint_path, checkpoint)

        self._summary(stats, time.monotonic() - started)
        if audit and (stats['missing_records'] or stats['ambiguous_records'] or stats['actors_failed']):
            raise CommandError('legacy completion audit failed')
