"""Ladder models. Same app and table names as when they lived in games.models."""

from django.conf import settings
from django.db import models


class RaddleUiState(models.Model):
    """Persistent, non-authoritative Raddle draft and clue display state.

    This deliberately has a separate row from ``ChainTaskState``.  UI
    autosave may lock this row, but must never serialize authoritative answer
    submission or overwrite checker state.
    """

    team = models.ForeignKey(
        'games.Team', related_name='raddle_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User', related_name='raddle_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey('games.Task', related_name='raddle_ui_states', on_delete=models.CASCADE)
    game = models.ForeignKey('games.Game', related_name='raddle_ui_states', on_delete=models.CASCADE)
    replay_slot = models.ForeignKey(
        'games.ReplaySlot', related_name='raddle_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    replay_run_id = models.UUIDField(blank=True, null=True, editable=False)
    actor_key = models.CharField(max_length=128, editable=False)
    namespace_key = models.CharField(max_length=128, editable=False)
    game_mode = models.CharField(max_length=20)
    drafts = models.JSONField(default=dict, blank=True)
    clue_marks = models.JSONField(default=dict, blank=True)
    updated_by_profile = models.ForeignKey(
        'games.Profile', related_name='updated_raddle_ui_states', blank=True, null=True,
        on_delete=models.SET_NULL, verbose_name='last updated by profile',
    )
    revision = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'games'
        constraints = [
            models.UniqueConstraint(
                fields=['actor_key', 'task', 'game', 'game_mode', 'namespace_key'],
                name='unique_raddle_ui_actor_context',
            ),
            models.UniqueConstraint(
                fields=['team', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'],
                condition=models.Q(team__isnull=False),
                name='unique_raddle_ui_team_game',
            ),
            models.UniqueConstraint(
                fields=['user', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'],
                condition=models.Q(user__isnull=False),
                name='unique_raddle_ui_user_game',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'task', 'game', 'game_mode', 'replay_slot', 'replay_run_id'],
                condition=models.Q(anon_key__isnull=False),
                name='unique_raddle_ui_anon_game',
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(team__isnull=False) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=False) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=False))
                ),
                name='raddle_ui_exactly_one_actor',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'task', 'game_mode'], name='games_raddle_ui_game_task_idx'),
            models.Index(fields=['replay_slot', 'replay_run_id', 'task', 'game'], name='games_raddle_ui_replay_idx'),
        ]

    def save(self, *args, **kwargs):
        actor_values = [self.team_id, self.user_id, self.anon_key]
        if sum(value is not None and value != '' for value in actor_values) != 1:
            raise ValueError('RaddleUiState requires exactly one actor')
        if self.team_id is not None:
            self.actor_key = 'team:{}'.format(self.team_id)
        elif self.user_id is not None:
            self.actor_key = 'user:{}'.format(self.user_id)
        else:
            self.actor_key = 'anon:{}'.format(self.anon_key)
        self.namespace_key = (
            'replay:{}'.format(self.replay_run_id)
            if self.replay_run_id is not None else 'official'
        )
        return super().save(*args, **kwargs)


class LadderOffer(models.Model):
    """Предложение лесенки от пользователя (черновик → отправлена → принята).

    TaskGroup/Task живут в игре ``ladder`` с самого начала. До accept нет
    GameTaskGroup в расписании — публичный доступ по ``/ladder/<share_hash>/``.
    После accept тот же TaskGroup получает номер в расписании; Attempt/Like сохраняются.
    """

    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_ACCEPTED = 'accepted'
    STATUS_VARIANTS = (
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SENT, 'Отправлена'),
        (STATUS_ACCEPTED, 'Принята'),
    )

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='ladder_offers',
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_VARIANTS,
        default=STATUS_DRAFT,
        db_index=True,
    )
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    intro = models.TextField(blank=True, default='')
    author = models.CharField(max_length=200, blank=True, default='')
    comment = models.TextField(
        blank=True,
        default='',
        verbose_name='комментарий для Андрея',
    )
    admin_note = models.TextField(
        blank=True,
        default='',
        verbose_name='заметка Андрея при возврате на доработку',
    )
    mixed_script = models.BooleanField(default=False)
    task_group = models.OneToOneField(
        'games.TaskGroup',
        related_name='ladder_offer',
        on_delete=models.CASCADE,
    )
    accepted_link = models.ForeignKey(
        'games.GameTaskGroup',
        related_name='accepted_from_offers',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        app_label = 'games'
        ordering = ['-updated_at']
        verbose_name = 'предложение лесенки'
        verbose_name_plural = 'предложения лесенок'

    def __str__(self):
        return 'LadderOffer #{} [{}] {}'.format(self.pk, self.status, self.author or self.user_id)

    @property
    def status_label(self):
        if self.status == self.STATUS_DRAFT and (self.sent_at or self.accepted_at):
            return 'На доработке'
        return dict(self.STATUS_VARIANTS).get(self.status, self.status)

    def can_author_edit(self):
        if self.status != self.STATUS_DRAFT:
            return False
        # Уже вышедшую прод-лесенку автор не правит напрямую (даже на доработке).
        if self.accepted_link_id:
            try:
                number = int(self.accepted_link.number)
            except (TypeError, ValueError, AttributeError):
                number = None
            if number is not None:
                from games.ladder_daily import is_ladder_number_published
                from games.models import Game
                game = Game.objects.filter(pk='ladder').first()
                if game is not None and is_ladder_number_published(game, number):
                    return False
        return True

    def play_url(self):
        return '/ladder/{}/'.format(self.share_hash)

    def results_url(self):
        return '/ladder/{}/results/'.format(self.share_hash)
