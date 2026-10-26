import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

class DailyResultProjection(models.Model):
    """Derived canonical score for one actor on one scheduled section release.

    Attempts / HintAttempts / chain state remain the gameplay source of truth.
    This row is a rebuildable query projection used by aggregate standings.
    Actor identity deliberately follows the existing team/user/anon buckets;
    anonymous keys are stored verbatim and never collapsed to display names.
    """
    ACTOR_TEAM = 'team'
    ACTOR_USER = 'user'
    ACTOR_ANON = 'anon'
    ACTOR_CHOICES = (
        (ACTOR_TEAM, 'Team'),
        (ACTOR_USER, 'Profile'),
        (ACTOR_ANON, 'Anonymous'),
    )

    game = models.ForeignKey('Game', related_name='daily_result_projections', on_delete=models.CASCADE)
    task_group = models.ForeignKey('TaskGroup', related_name='daily_result_projections', on_delete=models.CASCADE)
    actor_type = models.CharField(max_length=8, choices=ACTOR_CHOICES)
    actor_key = models.CharField(max_length=100)
    team = models.ForeignKey('Team', related_name='daily_result_projections', blank=True, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='daily_result_projections', blank=True, null=True, on_delete=models.CASCADE)
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    score = models.DecimalField(max_digits=12, decimal_places=3)
    attempts_count = models.PositiveIntegerField(default=0)
    fallback_duration_seconds = models.PositiveIntegerField(
        default=0,
        help_text='Rebuildable first-to-last attempt duration when no timer row exists.',
    )
    is_prepublication = models.BooleanField(default=False, help_text='Rebuildable eligibility metadata from canonical first-play data.')
    projected_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('game', 'task_group', 'team'), condition=models.Q(team__isnull=False), name='uniq_daily_proj_team'),
            models.UniqueConstraint(fields=('game', 'task_group', 'user'), condition=models.Q(user__isnull=False), name='uniq_daily_proj_user'),
            models.UniqueConstraint(fields=('game', 'task_group', 'anon_key'), condition=models.Q(anon_key__isnull=False), name='uniq_daily_proj_anon'),
            models.CheckConstraint(
                check=(
                    models.Q(actor_type='team', team__isnull=False, user__isnull=True, anon_key__isnull=True)
                    | models.Q(actor_type='user', team__isnull=True, user__isnull=False, anon_key__isnull=True)
                    | models.Q(actor_type='anon', team__isnull=True, user__isnull=True, anon_key__isnull=False)
                ),
                name='daily_proj_actor_shape',
            ),
        ]
        indexes = [
            models.Index(fields=('game', 'task_group', 'actor_type', 'actor_key'), name='games_drp_release_actor_idx'),
            models.Index(fields=('actor_type', 'actor_key', 'game'), name='games_drp_actor_game_idx'),
        ]

    def __str__(self):
        return '{} · {} · {}:{} = {}'.format(self.game_id, self.task_group_id, self.actor_type, self.actor_key, self.score)


class DailyTaskResultProjection(models.Model):
    """Derived result cell for one actor on one task in a section release.

    ``DailyResultProjection`` stores the release total used by aggregate
    standings.  This model keeps the smaller task-level read model needed by
    the ordinary results table.  Attempts, hint attempts, and chain state stay
    canonical; all fields here are rebuildable display/scoring data.
    """
    ACTOR_TEAM = DailyResultProjection.ACTOR_TEAM
    ACTOR_USER = DailyResultProjection.ACTOR_USER
    ACTOR_ANON = DailyResultProjection.ACTOR_ANON
    ACTOR_CHOICES = DailyResultProjection.ACTOR_CHOICES

    game = models.ForeignKey('Game', related_name='daily_task_result_projections', on_delete=models.CASCADE)
    task_group = models.ForeignKey('TaskGroup', related_name='daily_task_result_projections', on_delete=models.CASCADE)
    task = models.ForeignKey('Task', related_name='daily_task_result_projections', on_delete=models.CASCADE)
    actor_type = models.CharField(max_length=8, choices=ACTOR_CHOICES)
    actor_key = models.CharField(max_length=100)
    team = models.ForeignKey('Team', related_name='daily_task_result_projections', blank=True, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='daily_task_result_projections', blank=True, null=True, on_delete=models.CASCADE)
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    result_points = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    best_status = models.CharField(max_length=32, blank=True, default='')
    best_attempt_at = models.DateTimeField(blank=True, null=True)
    attempts_count = models.PositiveIntegerField(default=0)
    has_pending = models.BooleanField(default=False)
    hint_numbers = models.JSONField(default=list, blank=True)
    projected_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('game', 'task_group', 'task', 'team'),
                condition=models.Q(team__isnull=False),
                name='uniq_daily_task_proj_team',
            ),
            models.UniqueConstraint(
                fields=('game', 'task_group', 'task', 'user'),
                condition=models.Q(user__isnull=False),
                name='uniq_daily_task_proj_user',
            ),
            models.UniqueConstraint(
                fields=('game', 'task_group', 'task', 'anon_key'),
                condition=models.Q(anon_key__isnull=False),
                name='uniq_daily_task_proj_anon',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(actor_type='team', team__isnull=False, user__isnull=True, anon_key__isnull=True)
                    | models.Q(actor_type='user', team__isnull=True, user__isnull=False, anon_key__isnull=True)
                    | models.Q(actor_type='anon', team__isnull=True, user__isnull=True, anon_key__isnull=False)
                ),
                name='daily_task_proj_actor_shape',
            ),
        ]
        indexes = [
            models.Index(
                fields=('game', 'task_group', 'actor_type', 'actor_key'),
                name='games_dtrp_release_actor_idx',
            ),
            models.Index(
                fields=('game', 'task_group', 'task'),
                name='games_dtrp_release_task_idx',
            ),
        ]


class DailyResultProjectionState(models.Model):
    """Durable validity/coverage marker for one materialized release.

    ``DailyResultProjection`` is a rebuildable read model.  This row is the
    authority for whether the read model may be used: a few projection rows
    are never sufficient evidence of coverage.
    """
    game = models.ForeignKey('Game', related_name='daily_result_projection_states', on_delete=models.CASCADE)
    task_group = models.ForeignKey('TaskGroup', related_name='daily_result_projection_states', on_delete=models.CASCADE)
    completed_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(blank=True, null=True)
    published_version = models.UUIDField(blank=True, null=True)
    adapter_version = models.PositiveIntegerField(default=1)
    source_revision = models.PositiveBigIntegerField(default=0)
    coverage_complete = models.BooleanField(default=False, db_index=True)
    is_valid = models.BooleanField(default=False, db_index=True)
    full_refresh_required = models.BooleanField(default=True, db_index=True)
    pending_actor_refreshes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('game', 'task_group'), name='uniq_daily_proj_state_release')]


class DailyResultProjectionDirtyActor(models.Model):
    """Durable optimistic work item for one actor projection refresh."""
    game = models.ForeignKey('Game', related_name='daily_result_projection_dirty_actors', on_delete=models.CASCADE)
    task_group = models.ForeignKey('TaskGroup', related_name='daily_result_projection_dirty_actors', on_delete=models.CASCADE)
    actor_type = models.CharField(max_length=8)
    actor_key = models.CharField(max_length=160)
    source_revision = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('game', 'task_group', 'actor_type', 'actor_key'),
            name='uniq_daily_proj_dirty_actor',
        )]
        indexes = [models.Index(
            fields=('game', 'task_group', 'source_revision'),
            name='games_drp_dirty_release_idx',
        )]


class DailyResultProjectionRefreshOutbox(models.Model):
    """Transactional wake-up for projection work, separate from its hot state row."""

    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает отправки'),
        (STATUS_SENDING, 'Отправляется'),
        (STATUS_SENT, 'Отправлено'),
    )

    game = models.ForeignKey(
        'Game', related_name='daily_result_projection_outbox', on_delete=models.CASCADE,
    )
    task_group = models.ForeignKey(
        'TaskGroup', related_name='daily_result_projection_outbox', on_delete=models.CASCADE,
    )
    actor_type = models.CharField(max_length=8)
    actor_key = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    prepared_revision = models.PositiveBigIntegerField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'next_attempt_at'], name='games_drpo_due_idx'),
            models.Index(fields=['status', 'claimed_until'], name='games_drpo_claim_idx'),
            models.Index(fields=['status', 'sent_at'], name='games_drpo_sent_idx'),
        ]


class DailyMonthlyResultProjectionState(models.Model):
    """Version marker for a durable monthly actor aggregate."""
    game = models.ForeignKey('Game', related_name='daily_monthly_result_projection_states', on_delete=models.CASCADE)
    month = models.DateField()
    source_signature = models.CharField(max_length=64, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('game', 'month'), name='uniq_daily_monthly_proj_state',
        )]


class DailyMonthlyResultProjection(models.Model):
    """Rebuildable actor totals for one scheduled game and calendar month."""
    game = models.ForeignKey('Game', related_name='daily_monthly_result_projections', on_delete=models.CASCADE)
    month = models.DateField()
    actor_type = models.CharField(max_length=8, choices=DailyResultProjection.ACTOR_CHOICES)
    actor_key = models.CharField(max_length=100)
    team = models.ForeignKey('Team', related_name='+', blank=True, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='+', blank=True, null=True, on_delete=models.CASCADE)
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    score = models.DecimalField(max_digits=14, decimal_places=3)
    attempts_count = models.PositiveIntegerField(default=0)
    played_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('game', 'month', 'actor_type', 'actor_key'),
            name='uniq_daily_monthly_proj_actor',
        )]


class DailyAggregateSnapshot(models.Model):
    """Durable refresh request and publication pointer for one monthly table."""
    game = models.ForeignKey('Game', on_delete=models.CASCADE)
    month = models.DateField()
    published_version = models.ForeignKey(
        'DailyAggregateSnapshotVersion', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='+',
    )
    requested_revision = models.PositiveBigIntegerField(default=1)
    completed_revision = models.PositiveBigIntegerField(default=0)
    requested_signature = models.CharField(max_length=64, blank=True, default='')
    requested_at = models.DateTimeField(default=timezone.now)
    checked_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    lease_until = models.DateTimeField(blank=True, null=True)
    lease_token = models.UUIDField(blank=True, null=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('game', 'month'), name='uniq_daily_aggregate_snapshot',
        )]


class DailyAggregateSnapshotVersion(models.Model):
    """Immutable columns and source versions shared by every displayed page."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(
        DailyAggregateSnapshot, on_delete=models.CASCADE, related_name='versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True, db_index=True)
    columns = models.JSONField(default=list)
    source_versions = models.JSONField(default=dict)
    dependency_signature = models.CharField(max_length=64, blank=True, default='')
    missing_releases = models.JSONField(default=list)


class DailyAggregateSnapshotRow(models.Model):
    """One compact actor row; attempts are never needed by the HTTP reader."""
    version = models.ForeignKey(
        DailyAggregateSnapshotVersion, on_delete=models.CASCADE, related_name='rows',
    )
    actor_type = models.CharField(max_length=8, choices=DailyResultProjection.ACTOR_CHOICES)
    actor_key = models.CharField(max_length=160)
    team = models.ForeignKey('Team', related_name='+', blank=True, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='+', blank=True, null=True, on_delete=models.CASCADE)
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    score = models.DecimalField(max_digits=14, decimal_places=3)
    attempts_count = models.PositiveIntegerField(default=0)
    played_count = models.PositiveIntegerField(default=0)
    total_time_ms = models.DecimalField(max_digits=20, decimal_places=3, blank=True, null=True)
    used_fallback_time = models.BooleanField(default=False)
    cells = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=('version', 'score'), name='agg_snap_ver_score_idx')]
        constraints = [models.UniqueConstraint(
            fields=('version', 'actor_type', 'actor_key'), name='uniq_aggregate_snapshot_actor',
        )]


class DailyGameDifficulty(models.Model):
    """Cached, explainable difficulty for one scheduled daily-game edition.

    Pages only read this snapshot. Recalculation is a separate cron that claims
    due rows in the database; see docs/difficulty-cache.md.
    """

    id = models.AutoField(primary_key=True)
    placement = models.OneToOneField(
        'GameTaskGroup',
        related_name='difficulty',
        on_delete=models.CASCADE,
    )
    n = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)
    stars = models.PositiveSmallIntegerField(blank=True, null=True)
    is_preliminary = models.BooleanField(default=False)
    # Denormalized: True iff data_revision > calculated_revision. Kept so MySQL
    # can index due rows without a partial-index WHERE clause.
    dirty = models.BooleanField(default=True, db_index=True)
    calculated_at = models.DateTimeField(blank=True, null=True, db_index=True)
    published_at = models.DateTimeField(blank=True, null=True)
    data_revision = models.PositiveIntegerField(default=1)
    calculated_revision = models.PositiveIntegerField(default=0)
    refresh_not_before = models.DateTimeField(blank=True, null=True)
    refresh_claim_token = models.UUIDField(blank=True, null=True)
    refresh_claimed_until = models.DateTimeField(blank=True, null=True)
    refresh_fail_count = models.PositiveIntegerField(default=0)
    refresh_last_error = models.TextField(blank=True, default='')
    norm_version = models.PositiveIntegerField(default=0)
    # Last edit of one of the edition's tasks; restarts the fast refresh cadence.
    content_changed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['placement__game_id', 'placement__number']
        verbose_name = 'сложность ежедневной игры'
        verbose_name_plural = 'сложность ежедневных игр'
        indexes = [
            models.Index(
                fields=['dirty', 'refresh_not_before'],
                name='games_dgd_due_idx',
            ),
        ]

    def __str__(self):
        stars = '—' if self.stars is None else '{}{}'.format(
            '★' * self.stars,
            '☆' * (5 - self.stars),
        )
        return '{} · N={} · {}'.format(self.placement, self.n, stars)


class DailyDifficultyActorResult(models.Model):
    """One player's outcome on one daily edition.

    Kept so an edition's difficulty can compare every returning player with
    their own earlier editions of the same game. Rewritten with each snapshot.
    """

    id = models.BigAutoField(primary_key=True)
    placement = models.ForeignKey(
        'GameTaskGroup',
        related_name='difficulty_actor_results',
        on_delete=models.CASCADE,
    )
    game_id = models.CharField(max_length=100)
    published_at = models.DateTimeField(blank=True, null=True)
    actor = models.CharField(max_length=80)
    completed = models.BooleanField(default=False)
    abandoned = models.BooleanField(default=False)
    # Solve time when completed; otherwise the active time played so far.
    time_seconds = models.FloatField(blank=True, null=True)
    attempts = models.PositiveIntegerField(blank=True, null=True)
    errors = models.PositiveIntegerField(blank=True, null=True)
    used_help = models.BooleanField(default=False)
    # Null until the outcome is known (30 minutes after the first attempt).
    unfinished = models.BooleanField(blank=True, null=True)

    class Meta:
        verbose_name = 'результат игрока для сложности'
        verbose_name_plural = 'результаты игроков для сложности'
        constraints = [
            models.UniqueConstraint(
                fields=['placement', 'actor'],
                name='games_ddar_placement_actor_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=['game_id', 'actor', 'published_at'],
                name='games_ddar_history_idx',
            ),
        ]

    def __str__(self):
        return '{} · {}'.format(self.placement_id, self.actor)


class GameDifficultyNorm(models.Model):
    """Cached same-type historical baselines used when rating one edition."""

    game_id = models.CharField(max_length=100, primary_key=True)
    typical_time = models.FloatField(blank=True, null=True)
    typical_attempts = models.FloatField(blank=True, null=True)
    typical_errors = models.FloatField(blank=True, null=True)
    typical_help_rate = models.FloatField(blank=True, null=True)
    typical_unfinished_rate = models.FloatField(blank=True, null=True)
    calculated_at = models.DateTimeField(blank=True, null=True)
    version = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'историческая норма сложности'
        verbose_name_plural = 'исторические нормы сложности'

    def __str__(self):
        return '{} · v{}'.format(self.game_id, self.version)


class DailyDifficultyQueueStatus(models.Model):
    """Singleton heartbeat written by the minute difficulty worker."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_started_at = models.DateTimeField(blank=True, null=True)
    last_finished_at = models.DateTimeField(blank=True, null=True)
    last_success_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, default='')
    last_refreshed_count = models.PositiveIntegerField(default=0)
    last_limit = models.PositiveIntegerField(default=0)
    last_worker = models.CharField(max_length=255, blank=True, default='')
    last_health_check_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = 'состояние очереди сложности'
        verbose_name_plural = 'состояние очереди сложности'

    def __str__(self):
        return self.last_worker or 'очередь сложности'


class StatisticsEvent(models.Model):
    """Generic product/ops analytics row (who / when / what / payload)."""

    KIND_ANON_ATTEMPTS_MIGRATED = 'anon_attempts_migrated'
    KIND_CENSORLY_LEXICAL_GUESS = 'censorly_lexical_guess'

    KIND_CHOICES = (
        (KIND_ANON_ATTEMPTS_MIGRATED, 'Перенос анонимных попыток'),
        (KIND_CENSORLY_LEXICAL_GUESS, 'Решение лексики Цензурок'),
    )

    id = models.AutoField(primary_key=True)
    # Known values: see KIND_* constants / KIND_CHOICES (choices not enforced in DB).
    kind = models.CharField(max_length=64, db_index=True)
    time = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        'auth.User',
        related_name='statistics_events',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-time']
        indexes = [
            models.Index(fields=['kind', 'time'], name='games_stat_kind_time_idx'),
        ]
        verbose_name = 'событие статистики'
        verbose_name_plural = 'события статистики'

    def __str__(self):
        who = self.user_id or '—'
        return '{} [{}] user={}'.format(
            self.time.strftime('%Y-%m-%d %H:%M:%S') if self.time else '—',
            self.kind,
            who,
        )

    @classmethod
    def record(cls, kind, user=None, **payload):
        return cls.objects.create(kind=kind, user=user, payload=payload or {})


class PlayerStartedGame(models.Model):
    """One unique started game instance for one analytics actor."""

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(
        'Team',
        related_name='started_games',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User',
        related_name='started_games',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    game = models.ForeignKey(
        'Game',
        related_name='started_games',
        on_delete=models.CASCADE,
    )
    task_group = models.ForeignKey(
        'TaskGroup',
        related_name='started_games',
        on_delete=models.CASCADE,
    )
    game_kind = models.CharField(max_length=100, db_index=True)
    game_instance_id = models.CharField(max_length=128, db_index=True)
    public_game_id = models.CharField(max_length=128, blank=True, default='')
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metrika_acked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    is_backfilled = models.BooleanField(default=False, db_index=True)
    instrumentation_version = models.PositiveSmallIntegerField(blank=True, null=True)

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['team', 'game_instance_id'],
                name='uniq_started_game_team_instance',
            ),
            models.UniqueConstraint(
                fields=['user', 'game_instance_id'],
                name='uniq_started_game_user_instance',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'game_instance_id'],
                name='uniq_started_game_anon_instance',
            ),
        ]
        indexes = [
            models.Index(fields=['team', 'started_at'], name='games_psg_team_start_idx'),
            models.Index(fields=['user', 'started_at'], name='games_psg_user_start_idx'),
            models.Index(fields=['anon_key', 'started_at'], name='games_psg_anon_start_idx'),
            models.Index(fields=['game_kind', 'started_at'], name='games_psg_kind_start_idx'),
        ]
        verbose_name = 'начатая игра игрока'
        verbose_name_plural = 'начатые игры игроков'

    def __str__(self):
        actor = self.team or self.user or self.anon_key or '—'
        return '{} · {}'.format(actor, self.game_instance_id)


class PlayerCompletedGame(models.Model):
    """One unique completed game instance for one actor."""

    RESULT_SOLVED = 'solved'
    RESULT_COMPLETED = 'completed'
    RESULT_FAILED = 'failed'
    RESULT_CHOICES = (
        (RESULT_SOLVED, 'Solved'),
        (RESULT_COMPLETED, 'Completed'),
        (RESULT_FAILED, 'Failed'),
    )

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(
        'Team',
        related_name='completed_games',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User',
        related_name='completed_games',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    game = models.ForeignKey(
        'Game',
        related_name='completed_games',
        on_delete=models.CASCADE,
    )
    task_group = models.ForeignKey(
        'TaskGroup',
        related_name='completed_games',
        on_delete=models.CASCADE,
    )
    game_kind = models.CharField(max_length=32, db_index=True)
    game_instance_id = models.CharField(max_length=128, db_index=True)
    public_game_id = models.CharField(max_length=128, blank=True, default='')
    result = models.CharField(
        max_length=16,
        choices=RESULT_CHOICES,
        default=RESULT_COMPLETED,
    )
    completed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metrika_acked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    is_backfilled = models.BooleanField(default=False, db_index=True)
    instrumentation_version = models.PositiveSmallIntegerField(blank=True, null=True)

    class Meta:
        ordering = ['-completed_at']
        constraints = [
            models.UniqueConstraint(
                fields=['team', 'game_instance_id'],
                name='uniq_completed_game_team_instance',
            ),
            models.UniqueConstraint(
                fields=['user', 'game_instance_id'],
                name='uniq_completed_game_user_instance',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'game_instance_id'],
                name='uniq_completed_game_anon_instance',
            ),
        ]
        indexes = [
            models.Index(fields=['team', 'completed_at'], name='games_playe_team_id_6d898f_idx'),
            models.Index(fields=['user', 'completed_at'], name='games_playe_user_id_1b98cf_idx'),
            models.Index(fields=['anon_key', 'completed_at'], name='games_playe_anon_ke_7287be_idx'),
            models.Index(fields=['game_kind', 'completed_at'], name='games_playe_game_ki_ff625d_idx'),
        ]
        verbose_name = 'завершённая игра игрока'
        verbose_name_plural = 'завершённые игры игроков'

    def __str__(self):
        actor = self.team or self.user or self.anon_key or '—'
        return '{} · {} · {}'.format(actor, self.game_instance_id, self.result)


class DailySolveTiming(models.Model):
    """Canonical active-solving time for one actor on one official daily game."""

    STATUS_RUNNING = 'running'
    STATUS_AUTO_PAUSED = 'auto_paused'
    STATUS_MANUALLY_PAUSED = 'manually_paused'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = (
        (STATUS_RUNNING, 'Running'),
        (STATUS_AUTO_PAUSED, 'Auto-paused'),
        (STATUS_MANUALLY_PAUSED, 'Manually paused'),
        (STATUS_COMPLETED, 'Completed'),
    )

    TIMING_VERSION_ACTIVE = 1

    user = models.ForeignKey(
        'auth.User',
        related_name='daily_solve_timings',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        'Team',
        related_name='daily_solve_timings',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    # Non-null namespace key makes Team×release uniqueness enforceable on
    # MySQL, where conditional unique indexes are not supported. 'first' is
    # the statistical play; replay rows use their ReplaySlot primary key.
    team_timing_key = models.CharField(max_length=64, default='first', blank=True)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    game = models.ForeignKey(
        'Game',
        related_name='daily_solve_timings',
        on_delete=models.CASCADE,
    )
    task_group = models.ForeignKey(
        'TaskGroup',
        related_name='daily_solve_timings',
        on_delete=models.CASCADE,
    )
    replay_slot = models.ForeignKey(
        'ReplaySlot', related_name='daily_timings',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    timing_version = models.PositiveSmallIntegerField(default=TIMING_VERSION_ACTIVE)
    accumulated_ms = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_AUTO_PAUSED,
        db_index=True,
    )
    active_session_id = models.UUIDField(blank=True, null=True)
    interval_started_at = models.DateTimeField(blank=True, null=True)
    last_heartbeat_at = models.DateTimeField(blank=True, null=True)
    active_sessions_count = models.PositiveIntegerField(default=0)
    team_interval_started_at = models.DateTimeField(blank=True, null=True)
    last_seq = models.BigIntegerField(default=0)
    last_event_id = models.CharField(max_length=64, blank=True, default='')
    applied_event_ids = models.JSONField(default=list, blank=True)
    frozen_ms = models.BigIntegerField(blank=True, null=True)
    # Part of accumulated_ms that rests on the client's claim after its lease
    # went stale (lost heartbeats), not on server-confirmed heartbeats.
    client_credited_ms = models.BigIntegerField(default=0)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'game', 'task_group', 'replay_slot'],
                condition=models.Q(user__isnull=False),
                name='uniq_daily_timing_user_game_tg',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'game', 'task_group', 'replay_slot'],
                condition=models.Q(anon_key__isnull=False),
                name='uniq_daily_timing_anon_game_tg',
            ),
            models.UniqueConstraint(
                fields=['team', 'game', 'task_group', 'team_timing_key'],
                name='uniq_daily_timing_team_key',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(user__isnull=False, team__isnull=True, anon_key__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=False, anon_key__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=True, anon_key__isnull=False)
                ),
                name='daily_timing_actor_shape',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'task_group'], name='games_dst_game_tg_idx'),
            models.Index(fields=['user', 'game'], name='games_dst_user_game_idx'),
            models.Index(fields=['team', 'game'], name='games_dst_team_game_idx'),
            models.Index(fields=['anon_key', 'game'], name='games_dst_anon_game_idx'),
            models.Index(fields=['replay_slot', 'game', 'task_group'], name='games_dst_replay_game_tg_idx'),
        ]
        verbose_name = 'время прохождения ежедневной игры'
        verbose_name_plural = 'времена прохождения ежедневных игр'

    def __str__(self):
        actor = self.team or self.user or self.anon_key or '—'
        return '{} · {} · {}'.format(actor, self.game_id, self.status)


class DailyTimingEvent(models.Model):
    """Immutable client timing event used to rebuild the timing read-model."""

    ACTION_CHOICES = (
        ('start', 'Start'),
        ('resume', 'Resume'),
        ('heartbeat', 'Heartbeat'),
        ('auto_pause', 'Auto-pause'),
        ('pause', 'Pause'),
        ('complete', 'Complete'),
        ('activity', 'Gameplay activity'),
    )

    game = models.ForeignKey('Game', related_name='daily_timing_events', on_delete=models.CASCADE)
    task_group = models.ForeignKey('TaskGroup', related_name='daily_timing_events', on_delete=models.CASCADE)
    user = models.ForeignKey(
        'auth.User', related_name='daily_timing_events', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        'Team', related_name='daily_timing_events', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    replay_slot = models.ForeignKey(
        'ReplaySlot', related_name='daily_timing_events', blank=True, null=True,
        on_delete=models.CASCADE,
    )
    # CharField deliberately matches the historical endpoint contract: old
    # clients occasionally used timestamp-like IDs instead of UUIDs.
    session_id = models.CharField(max_length=128)
    event_id = models.CharField(max_length=128)
    actor_key = models.CharField(max_length=160, default='')
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    seq = models.BigIntegerField(default=0)
    claimed_ms = models.BigIntegerField(blank=True, null=True)
    # Optional client ordering hint for future replay.  Server-side timing
    # remains authoritative and continues to use ``occurred_at``.
    client_occurred_at = models.DateTimeField(blank=True, null=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'task_group', 'actor_key', 'session_id', 'event_id'],
                name='uniq_daily_timing_event_id',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(user__isnull=False, team__isnull=True, anon_key__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=False, anon_key__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=True, anon_key__isnull=False)
                ),
                name='daily_timing_event_actor_shape',
            ),
        ]
        indexes = [
            models.Index(
                fields=['game', 'task_group', 'session_id', 'seq'],
                name='games_dte_session_seq_idx',
            ),
            models.Index(
                fields=['game', 'task_group', 'occurred_at'],
                name='games_dte_release_time_idx',
            ),
            models.Index(
                fields=['game', 'task_group', 'actor_key', 'session_id', 'occurred_at'],
                name='games_dte_audit_order_idx',
            ),
        ]

    def __str__(self):
        return '{} · {} · {} · {}'.format(
            self.game_id, self.task_group_id, self.action, self.event_id,
        )


class DailySolveTimingSession(models.Model):
    """One device/tab's participation in a daily timing row.

    Timing rows are aggregates over independent sessions.  A session pause or
    lease expiry must never take ownership away from another tab or device.
    """

    STATUS_RUNNING = 'running'
    STATUS_PAUSED = 'paused'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = (
        (STATUS_RUNNING, 'Running'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_CLOSED, 'Closed'),
    )

    timing = models.ForeignKey(
        DailySolveTiming,
        related_name='sessions',
        on_delete=models.CASCADE,
    )
    session_id = models.UUIDField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PAUSED)
    started_at = models.DateTimeField(blank=True, null=True)
    last_heartbeat_at = models.DateTimeField(blank=True, null=True)
    paused_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    close_reason = models.CharField(max_length=32, blank=True, default='')
    last_seq = models.BigIntegerField(default=0)
    last_event_id = models.CharField(max_length=64, blank=True, default='')
    applied_event_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['timing', 'session_id'],
                name='uniq_daily_timing_session',
            ),
        ]
        indexes = [
            models.Index(fields=['timing', 'status'], name='games_dt_session_status_idx'),
        ]

    def __str__(self):
        return '{} · {}'.format(self.timing_id, self.session_id)


class PlayerAnalyticsState(models.Model):
    """Per-actor product lifecycle markers (activation etc.)."""

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey(
        'Team',
        related_name='analytics_states',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User',
        related_name='analytics_states',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    signup_at = models.DateTimeField(blank=True, null=True)
    signup_method = models.CharField(max_length=32, blank=True, default='')
    signup_goal_acked_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    activation_is_backfilled = models.BooleanField(default=False)
    activation_goal_acked_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['team'],
                name='uniq_player_analytics_state_team',
            ),
            models.UniqueConstraint(
                fields=['user'],
                name='uniq_player_analytics_state_user',
            ),
            models.UniqueConstraint(
                fields=['anon_key'],
                name='uniq_player_analytics_state_anon',
            ),
        ]
        verbose_name = 'состояние продуктовой аналитики игрока'
        verbose_name_plural = 'состояния продуктовой аналитики игроков'

    def __str__(self):
        actor = self.team or self.user or self.anon_key or '—'
        return 'analytics[{}]'.format(actor)
