"""Which parts of client-side and offline play are enabled, and where."""

import logging

from django.core.cache import caches
from django.db import models

cache = caches['offline_play']
logger = logging.getLogger('games.offline')

# games.gameplay.policy caches the enabled rules under this key.
RULES_CACHE_KEY = 'offline_play_rules:v1'

PLAY_MODE_CHOICES = (
    ('', 'любой'),
    ('general', 'general'),
    ('tournament', 'tournament'),
)


class OfflinePlayRule(models.Model):
    """One rule of docs/offline-play-plan.md («Конфиг»).

    Empty ``game``, ``task_type`` and ``mode`` match anything; among the
    enabled rules that match a move the most specific one wins (game, then
    task type, then mode), and on a tie the most recently updated.
    """

    id = models.AutoField(primary_key=True)
    enabled = models.BooleanField(default=True)
    game = models.ForeignKey(
        'Game', related_name='offline_play_rules', blank=True, null=True, on_delete=models.CASCADE,
    )
    task_type = models.CharField(max_length=64, blank=True, default='')
    mode = models.CharField(max_length=16, blank=True, default='', choices=PLAY_MODE_CHOICES)
    instant_verdict = models.BooleanField(
        default=False, help_text='Страница сразу показывает верно/неверно, не дожидаясь сервера.',
    )
    offline_attempts = models.BooleanField(
        default=False, help_text='Ходы без сети через очередь (требует мгновенного вердикта).',
    )
    offline_timing = models.BooleanField(
        default=False, help_text='Засчитывать время офлайн-решения (требует офлайн-ходов).',
    )
    offline_hints = models.BooleanField(
        default=False, help_text='Подсказки без сети (требует офлайн-ходов).',
    )
    rollout_percent = models.PositiveSmallIntegerField(
        default=100, help_text='Доля игроков (0–100), стабильно по игроку.',
    )
    note = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'offline play rule'
        constraints = [
            models.CheckConstraint(
                check=models.Q(rollout_percent__lte=100), name='offline_play_rule_rollout_percent_lte_100',
            ),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        _forget_cached_rules()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        _forget_cached_rules()
        return result

    def __str__(self):
        scope = ' / '.join(part for part in (self.game_id or '', self.task_type, self.mode) if part)
        return '{} ({})'.format(scope or 'все игры', 'вкл' if self.enabled else 'выкл')


def _forget_cached_rules():
    try:
        cache.delete(RULES_CACHE_KEY)
    except Exception:
        # Pages pick the change up when the cached rules expire (5 minutes).
        logger.exception('event=offline_play_rules_cache_failed')
