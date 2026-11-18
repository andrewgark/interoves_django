"""Replacements models. Same app as the rest of games.models."""

from django.db import models


class ReplacementsUiState(models.Model):
    """Shared, non-authoritative guesses of a replacements_lines task.

    Kept apart from ``ChainTaskState`` like ``RaddleUiState``: saving a draft
    must never lock or overwrite checker progress.
    """

    team = models.ForeignKey(
        'games.Team', related_name='replacements_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        'auth.User', related_name='replacements_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    task = models.ForeignKey('games.Task', related_name='replacements_ui_states', on_delete=models.CASCADE)
    game = models.ForeignKey('games.Game', related_name='replacements_ui_states', on_delete=models.CASCADE)
    replay_slot = models.ForeignKey(
        'games.ReplaySlot', related_name='replacements_ui_states',
        blank=True, null=True, on_delete=models.CASCADE,
    )
    replay_run_id = models.UUIDField(blank=True, null=True, editable=False)
    actor_key = models.CharField(max_length=128, editable=False)
    namespace_key = models.CharField(max_length=128, editable=False)
    game_mode = models.CharField(max_length=20)
    # {"<line>:<slot>": "guess"}; empty slots have no key.
    guesses = models.JSONField(default=dict, blank=True)
    # Task.attempt_revision the guesses were made for; an edited task drops them.
    task_revision = models.CharField(max_length=64, blank=True, default='')
    updated_by_profile = models.ForeignKey(
        'games.Profile', related_name='updated_replacements_ui_states', blank=True, null=True,
        on_delete=models.SET_NULL, verbose_name='last updated by profile',
    )
    revision = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'games'
        constraints = [
            models.UniqueConstraint(
                fields=['actor_key', 'task', 'game', 'game_mode', 'namespace_key'],
                name='unique_replacements_ui_actor_context',
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(team__isnull=False) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=False) & models.Q(anon_key__isnull=True))
                    | (models.Q(team__isnull=True) & models.Q(user__isnull=True) & models.Q(anon_key__isnull=False))
                ),
                name='replacements_ui_exactly_one_actor',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'task', 'game_mode'], name='games_repl_ui_game_task_idx'),
        ]

    @staticmethod
    def identity_keys(*, team=None, user=None, anon_key=None, replay_run_id=None):
        if team is not None:
            actor_key = 'team:{}'.format(getattr(team, 'pk', team))
        elif user is not None:
            actor_key = 'user:{}'.format(getattr(user, 'pk', user))
        else:
            actor_key = 'anon:{}'.format(anon_key)
        namespace_key = (
            'replay:{}'.format(replay_run_id)
            if replay_run_id is not None else 'official'
        )
        return actor_key, namespace_key

    def save(self, *args, **kwargs):
        actor_values = [self.team_id, self.user_id, self.anon_key]
        if sum(value is not None and value != '' for value in actor_values) != 1:
            raise ValueError('ReplacementsUiState requires exactly one actor')
        self.actor_key, self.namespace_key = self.identity_keys(
            team=self.team_id, user=self.user_id, anon_key=self.anon_key,
            replay_run_id=self.replay_run_id,
        )
        return super().save(*args, **kwargs)
