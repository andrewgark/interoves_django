from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.analytics import GAME_KIND_BY_ID
from games.models import (
    GameTaskGroup,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
)


MAX_INTERVAL = timedelta(days=31)
FUTURE_TOLERANCE = timedelta(minutes=5)
KNOWN_INSTRUMENTATION_VERSION = 2


def _valid_identity_q():
    return (
        Q(user_id__isnull=False, team_id__isnull=True, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=False, anon_key__isnull=True)
        | Q(user_id__isnull=True, team_id__isnull=True, anon_key__isnull=False)
    )


def _actor_slices(queryset):
    return (
        (
            'user',
            queryset.filter(
                user_id__isnull=False,
                team_id__isnull=True,
                anon_key__isnull=True,
            ),
            {
                'user_id': OuterRef('user_id'),
                'team_id__isnull': True,
                'anon_key__isnull': True,
            },
        ),
        (
            'team',
            queryset.filter(
                user_id__isnull=True,
                team_id__isnull=False,
                anon_key__isnull=True,
            ),
            {
                'team_id': OuterRef('team_id'),
                'user_id__isnull': True,
                'anon_key__isnull': True,
            },
        ),
        (
            'anonymous',
            queryset.filter(
                user_id__isnull=True,
                team_id__isnull=True,
                anon_key__isnull=False,
            ),
            {
                'anon_key': OuterRef('anon_key'),
                'user_id__isnull': True,
                'team_id__isnull': True,
            },
        ),
    )


class Command(BaseCommand):
    help = 'Run bounded, read-only invariant checks for product analytics rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since', required=True, help='Inclusive ISO datetime lower bound.'
        )
        parser.add_argument(
            '--until', required=True, help='Exclusive ISO datetime upper bound.'
        )

    def _parse_bound(self, raw, label):
        value = parse_datetime(raw)
        if value is None:
            raise CommandError('{} must be an ISO datetime'.format(label))
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def _duplicate_count(self, model, candidates):
        total = 0
        for _label, actor_candidates, actor_lookup in _actor_slices(candidates):
            earlier = model.objects.filter(
                game_id=OuterRef('game_id'),
                task_group_id=OuterRef('task_group_id'),
                pk__lt=OuterRef('pk'),
                **actor_lookup
            )
            total += actor_candidates.annotate(
                has_earlier_duplicate=Exists(earlier),
            ).filter(has_earlier_duplicate=True).count()
        return total

    def _completion_without_start_count(self, completions):
        total = 0
        for _label, actor_completions, actor_lookup in _actor_slices(completions):
            matching_start = PlayerStartedGame.objects.filter(
                game_id=OuterRef('game_id'),
                task_group_id=OuterRef('task_group_id'),
                game_instance_id=OuterRef('game_instance_id'),
                **actor_lookup
            )
            total += actor_completions.annotate(
                has_start=Exists(matching_start),
            ).filter(has_start=False).count()
        return total

    def _completion_before_start_count(self, completions):
        total = 0
        live_v2 = completions.filter(
            is_backfilled=False,
            instrumentation_version=KNOWN_INSTRUMENTATION_VERSION,
        )
        for _label, actor_completions, actor_lookup in _actor_slices(live_v2):
            first_start = (
                PlayerStartedGame.objects.filter(
                    game_id=OuterRef('game_id'),
                    task_group_id=OuterRef('task_group_id'),
                    game_instance_id=OuterRef('game_instance_id'),
                    **actor_lookup
                )
                .order_by('started_at', 'pk')
                .values('started_at')[:1]
            )
            total += actor_completions.annotate(
                first_started_at=Subquery(first_start),
            ).filter(first_started_at__gt=F('completed_at')).count()
        return total

    def _unknown_game_kind_count(self, candidates, *, completions):
        count = 0
        for game_id, game_kind in candidates.values_list('game_id', 'game_kind').iterator(
            chunk_size=2000
        ):
            if completions:
                expected = GAME_KIND_BY_ID.get(game_id)
            else:
                expected = GAME_KIND_BY_ID.get(game_id) or str(game_id or '')[:100] or None
            if expected is None or game_kind != expected:
                count += 1
        return count

    def _missing_placement_count(self, candidates):
        placement = GameTaskGroup.objects.filter(
            game_id=OuterRef('game_id'),
            task_group_id=OuterRef('task_group_id'),
        )
        return candidates.annotate(
            has_placement=Exists(placement),
        ).filter(has_placement=False).count()

    def _bad_instance_id_count(self, candidates):
        count = 0
        for game_id, task_group_id, instance_id in candidates.values_list(
            'game_id', 'task_group_id', 'game_instance_id'
        ).iterator(chunk_size=2000):
            if instance_id != '{}:{}'.format(game_id, task_group_id):
                count += 1
        return count

    def handle(self, *args, **options):
        since = self._parse_bound(options['since'], '--since')
        until = self._parse_bound(options['until'], '--until')
        if until <= since:
            raise CommandError('--until must be later than --since')
        if until - since > MAX_INTERVAL:
            raise CommandError('requested interval exceeds the maximum of 31 days')

        starts = PlayerStartedGame.objects.filter(
            started_at__gte=since,
            started_at__lt=until,
        )
        completions = PlayerCompletedGame.objects.filter(
            completed_at__gte=since,
            completed_at__lt=until,
        )
        states = PlayerAnalyticsState.objects.filter(
            updated_at__gte=since,
            updated_at__lt=until,
        )
        future_cutoff = timezone.now() + FUTURE_TOLERANCE

        checks = [
            ('start_identity_exactly_one', starts.exclude(_valid_identity_q()).count()),
            (
                'completion_identity_exactly_one',
                completions.exclude(_valid_identity_q()).count(),
            ),
            (
                'analytics_state_identity_exactly_one',
                states.exclude(_valid_identity_q()).count(),
            ),
            ('duplicate_starts', self._duplicate_count(PlayerStartedGame, starts)),
            (
                'duplicate_completions',
                self._duplicate_count(PlayerCompletedGame, completions),
            ),
            ('completion_without_start', self._completion_without_start_count(completions)),
            ('completion_before_start', self._completion_before_start_count(completions)),
            ('start_missing_placement', self._missing_placement_count(starts)),
            ('completion_missing_placement', self._missing_placement_count(completions)),
            ('start_bad_game_instance_id', self._bad_instance_id_count(starts)),
            ('completion_bad_game_instance_id', self._bad_instance_id_count(completions)),
            (
                'start_unknown_game_kind',
                self._unknown_game_kind_count(starts, completions=False),
            ),
            (
                'completion_unknown_game_kind',
                self._unknown_game_kind_count(completions, completions=True),
            ),
            (
                'start_timestamp_in_future',
                starts.filter(started_at__gt=future_cutoff).count(),
            ),
            (
                'completion_timestamp_in_future',
                completions.filter(completed_at__gt=future_cutoff).count(),
            ),
            (
                'start_unknown_instrumentation_version',
                starts.exclude(
                    Q(instrumentation_version__isnull=True)
                    | Q(instrumentation_version=KNOWN_INSTRUMENTATION_VERSION)
                ).count(),
            ),
            (
                'completion_unknown_instrumentation_version',
                completions.exclude(
                    Q(instrumentation_version__isnull=True)
                    | Q(instrumentation_version=KNOWN_INSTRUMENTATION_VERSION)
                ).count(),
            ),
            (
                'start_v2_marked_backfilled',
                starts.filter(
                    instrumentation_version=KNOWN_INSTRUMENTATION_VERSION,
                    is_backfilled=True,
                ).count(),
            ),
            (
                'completion_v2_marked_backfilled',
                completions.filter(
                    instrumentation_version=KNOWN_INSTRUMENTATION_VERSION,
                    is_backfilled=True,
                ).count(),
            ),
        ]

        self.stdout.write('check\tstatus\tcount')
        failed = 0
        for name, count in checks:
            status = 'PASS' if count == 0 else 'FAIL'
            failed += int(count > 0)
            self.stdout.write('{}\t{}\t{}'.format(name, status, count))
        self.stdout.write(
            'window\tINFO\t{} <= timestamp < {}'.format(
                since.isoformat(), until.isoformat()
            )
        )
        if failed:
            raise CommandError(
                'product analytics quality check failed: {} invariant(s) violated'.format(failed)
            )
        self.stdout.write(self.style.SUCCESS('product analytics quality check passed'))
