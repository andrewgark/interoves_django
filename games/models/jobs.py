import uuid

from django.db import models

class QueueWorkerHeartbeat(models.Model):
    """Latest durable heartbeat for a cron/queue worker."""

    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_SUCCESS, 'Успешно'),
        (STATUS_FAILED, 'Ошибка'),
    )

    queue_name = models.CharField(max_length=80, primary_key=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    worker = models.CharField(max_length=255, blank=True, default='')
    host = models.CharField(max_length=255, blank=True, default='')
    pid = models.PositiveIntegerField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    last_success_at = models.DateTimeField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    processed_count = models.IntegerField(blank=True, null=True)
    last_error = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'heartbeat очереди'
        verbose_name_plural = 'heartbeat очередей'

    def __str__(self):
        return '{} · {}'.format(self.queue_name, self.status)


class WordSaladRecheckJob(models.Model):
    """Durable actor-by-actor rebuild after editing a live Word Salad."""

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_SUPERSEDED = 'superseded'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает обработки'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_FAILED, 'Ошибка'),
        (STATUS_SUPERSEDED, 'Заменено новой версией'),
    )

    task = models.ForeignKey('Task', related_name='word_salad_recheck_jobs', on_delete=models.CASCADE)
    game = models.ForeignKey('Game', related_name='word_salad_recheck_jobs', on_delete=models.CASCADE)
    task_revision = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    pending_resolution = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    total_actors = models.PositiveIntegerField(default=0)
    completed_actors = models.PositiveIntegerField(default=0)
    credited_attempts = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['task', 'game', 'status'], name='games_wsrj_task_status_idx'),
            models.Index(fields=['status', 'next_attempt_at'], name='games_wsrj_due_idx'),
        ]

    @property
    def progress(self):
        if self.status == self.STATUS_COMPLETED:
            return 100
        if not self.total_actors:
            return 0
        return min(99, int(100 * self.completed_actors / self.total_actors))


class WordSaladRecheckItem(models.Model):
    """Snapshot of one actor to rebuild; actor membership must not drift."""

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_SUPERSEDED = 'superseded'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает обработки'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_FAILED, 'Ошибка'),
        (STATUS_SUPERSEDED, 'Заменено новой версией'),
    )

    job = models.ForeignKey(WordSaladRecheckJob, related_name='items', on_delete=models.CASCADE)
    actor_key = models.CharField(max_length=255)
    team = models.ForeignKey('Team', blank=True, null=True, on_delete=models.CASCADE)
    user = models.ForeignKey('auth.User', blank=True, null=True, on_delete=models.CASCADE)
    anon_key = models.CharField(max_length=64, blank=True, null=True)
    replay_slot = models.ForeignKey('ReplaySlot', blank=True, null=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    credited_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['job', 'actor_key'], name='games_wsrji_job_actor_uniq'),
        ]
        indexes = [
            models.Index(fields=['job', 'status', 'next_attempt_at'], name='games_wsrji_due_idx'),
        ]


class WordSaladRecheckOutbox(models.Model):
    """Transactional transport intent for one authoritative recheck item."""

    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает отправки'),
        (STATUS_SENDING, 'Отправляется'),
        (STATUS_SENT, 'Отправлено'),
        (STATUS_CANCELLED, 'Отменено'),
    )

    item = models.ForeignKey(
        WordSaladRecheckItem,
        related_name='outbox_rows',
        on_delete=models.CASCADE,
    )
    task_revision = models.UUIDField(db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['item', 'task_revision'],
                name='games_wsr_outbox_item_revision_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'next_attempt_at'], name='games_wsr_outbox_due_idx'),
            models.Index(fields=['status', 'claimed_until'], name='games_wsr_outbox_claim_idx'),
        ]


class AccountMerge(models.Model):
    """Audit record for one completed user-to-user account merge."""

    id = models.AutoField(primary_key=True)
    target_user = models.ForeignKey(
        'auth.User',
        related_name='account_merges_received',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    source_user = models.OneToOneField(
        'auth.User',
        related_name='account_merge_as_source',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    target_user_id_snapshot = models.PositiveIntegerField(db_index=True)
    source_user_id_snapshot = models.PositiveIntegerField(db_index=True)
    provider = models.CharField(max_length=32, blank=True, default='')
    provider_uid = models.CharField(max_length=191, blank=True, default='')
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'объединение аккаунтов'
        verbose_name_plural = 'объединения аккаунтов'

    def __str__(self):
        return 'account merge {} -> {}'.format(
            self.source_user_id_snapshot,
            self.target_user_id_snapshot,
        )


class AccountMergeJob(models.Model):
    """Durable queue job for an authenticated account merge."""

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает обработки'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_FAILED, 'Ошибка'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_user = models.ForeignKey(
        'auth.User', related_name='account_merge_jobs_target',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    source_user = models.ForeignKey(
        'auth.User', related_name='account_merge_jobs_source',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    target_user_id_snapshot = models.PositiveIntegerField(db_index=True)
    source_user_id_snapshot = models.PositiveIntegerField(db_index=True)
    provider = models.CharField(max_length=32, blank=True, default='')
    provider_uid = models.CharField(max_length=191, blank=True, default='')
    next_url = models.URLField(max_length=2048, blank=True, default='')
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES,
        default=STATUS_PENDING, db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    account_merge = models.OneToOneField(
        AccountMerge, related_name='job', blank=True, null=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=(
                    'target_user_id_snapshot', 'source_user_id_snapshot',
                    'provider', 'provider_uid',
                ),
                name='games_amj_identity_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'next_attempt_at'], name='games_acmj_due_idx'),
            models.Index(
                fields=['target_user_id_snapshot', 'status'],
                name='games_acmj_target_status_idx',
            ),
        ]


class AnonAccountClaim(models.Model):
    """Permanent ownership claim preventing one guest identity being stolen twice."""

    id = models.AutoField(primary_key=True)
    anon_key = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        'auth.User', related_name='anon_account_claims', on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'привязка гостевого прогресса'
        verbose_name_plural = 'привязки гостевого прогресса'

    def __str__(self):
        return '{} -> {}'.format(self.anon_key, self.user_id)


class AnonymousMergeJob(models.Model):
    """Durable server-side merge of one anonymous identity into one user."""

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает обработки'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_FAILED, 'Ошибка'),
    )

    STAGE_ANALYZING = 'analyzing'
    STAGE_MOVING = 'moving'
    STAGE_RECONCILING = 'reconciling'
    STAGE_FINALIZING = 'finalizing'
    STAGE_CHOICES = (
        (STAGE_ANALYZING, 'Подготовка'),
        (STAGE_MOVING, 'Перенос данных'),
        (STAGE_RECONCILING, 'Проверка прогресса'),
        (STAGE_FINALIZING, 'Завершение'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    anon_key = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        'auth.User', related_name='anonymous_merge_jobs', on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    stage = models.CharField(max_length=16, choices=STAGE_CHOICES, default=STAGE_ANALYZING)
    move_step = models.CharField(max_length=32, blank=True, default='')
    total_submissions = models.PositiveIntegerField(default=0)
    moved_submissions = models.PositiveIntegerField(default=0)
    moved_counts = models.JSONField(default=dict, blank=True)
    total_reconciliation_units = models.PositiveIntegerField(default=0)
    completed_reconciliation_units = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    event_recorded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at'], name='games_amj_due_idx'),
            models.Index(fields=['user', 'status'], name='games_amj_user_status_idx'),
        ]


class AnonymousMergeReconcileItem(models.Model):
    """One idempotent game/task-group reconciliation unit for a merge job."""

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает обработки'),
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_FAILED, 'Ошибка'),
    )

    id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(
        AnonymousMergeJob, related_name='reconcile_items', on_delete=models.CASCADE,
    )
    game = models.ForeignKey('Game', related_name='anonymous_merge_reconcile_items', on_delete=models.CASCADE)
    task_group = models.ForeignKey(
        'TaskGroup', related_name='anonymous_merge_reconcile_items', on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    claim_token = models.UUIDField(blank=True, null=True)
    claimed_until = models.DateTimeField(blank=True, null=True)
    next_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['job', 'game', 'task_group'],
                name='games_amri_job_game_group_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['job', 'status', 'next_attempt_at'], name='games_amri_due_idx'),
        ]
