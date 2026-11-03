from datetime import time

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

DEFAULT_QUIET_START = time(23, 0)
DEFAULT_QUIET_END = time(9, 0)


class Notification(models.Model):
    TG_NONE = 'none'
    TG_PENDING = 'pending'
    TG_SENDING = 'sending'
    TG_SENT = 'sent'
    TG_SKIPPED = 'skipped'
    TG_FAILED = 'failed'
    TG_STATES = (
        (TG_NONE, 'Не нужно'),
        (TG_PENDING, 'Ждёт отправки'),
        (TG_SENDING, 'Отправляется'),
        (TG_SENT, 'Отправлено'),
        (TG_SKIPPED, 'Пропущено'),
        (TG_FAILED, 'Ошибка'),
    )

    user = models.ForeignKey(User, related_name='notifications', on_delete=models.CASCADE)
    kind = models.CharField(max_length=64)
    # Unread notifications with the same key fold into one row.
    group_key = models.CharField(max_length=160)
    actor = models.ForeignKey(User, related_name='+', null=True, blank=True, on_delete=models.SET_NULL)
    actor_count = models.PositiveIntegerField(default=1)
    # Ids of the objects involved, never rendered text.
    payload = models.JSONField(default=dict, blank=True)
    # False when the player switched the group off on the site but kept
    # Telegram: the row then exists only to carry the Telegram copy.
    in_inbox = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    tg_state = models.CharField(max_length=16, choices=TG_STATES, default=TG_NONE)
    tg_due_at = models.DateTimeField(null=True, blank=True)
    tg_enqueued_at = models.DateTimeField(null=True, blank=True)
    tg_sent_at = models.DateTimeField(null=True, blank=True)
    tg_reason = models.CharField(max_length=32, blank=True, default='')

    class Meta:
        verbose_name = 'уведомление'
        verbose_name_plural = 'уведомления'
        indexes = [
            models.Index(fields=['user', '-updated_at'], name='games_notif_user_updated_idx'),
            models.Index(fields=['user', 'group_key', 'read_at'], name='games_notif_group_idx'),
            models.Index(fields=['tg_state', 'tg_due_at'], name='games_notif_tg_due_idx'),
        ]

    def __str__(self):
        return 'Notification #{} {} → {}'.format(self.pk, self.kind, self.user_id)


class NotificationSetting(models.Model):
    """A player's override for one group; a missing row means the group default."""

    user = models.ForeignKey(User, related_name='notification_settings', on_delete=models.CASCADE)
    group = models.CharField(max_length=32)
    site = models.BooleanField(default=True)
    telegram = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'настройка уведомлений'
        verbose_name_plural = 'настройки уведомлений'
        constraints = [
            models.UniqueConstraint(fields=['user', 'group'], name='games_notifsetting_uniq'),
        ]

    def __str__(self):
        return '{} {} site={} tg={}'.format(self.user_id, self.group, self.site, self.telegram)


class NotificationPrefs(models.Model):
    """Per-player delivery state. A missing row means defaults."""

    user = models.OneToOneField(User, related_name='notification_prefs', primary_key=True, on_delete=models.CASCADE)
    quiet_hours_enabled = models.BooleanField(default=True)
    # Wall-clock times in Profile.timezone; the interval may cross midnight.
    quiet_start = models.TimeField(default=DEFAULT_QUIET_START)
    quiet_end = models.TimeField(default=DEFAULT_QUIET_END)
    # The bell badge counts what arrived after the dropdown was last opened.
    seen_at = models.DateTimeField(null=True, blank=True)
    # Set when Telegram answers 403: the player blocked the bot.
    telegram_blocked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'параметры уведомлений'
        verbose_name_plural = 'параметры уведомлений'

    def __str__(self):
        return 'NotificationPrefs {}'.format(self.user_id)


class DailyReminder(models.Model):
    """«Today's edition is out» in Telegram for one daily game. No row means off."""

    MODE_CHANNEL = 'channel'
    MODE_RELEASE = 'release'
    MODE_CUSTOM = 'custom'
    MODES = (
        (MODE_CHANNEL, 'Как в канале'),
        (MODE_RELEASE, 'Сразу после выхода'),
        (MODE_CUSTOM, 'В своё время'),
    )

    user = models.ForeignKey(User, related_name='daily_reminders', on_delete=models.CASCADE)
    game_id = models.CharField(max_length=32)
    mode = models.CharField(max_length=16, choices=MODES, default=MODE_CHANNEL)
    # Wall-clock time in Profile.timezone, for MODE_CUSTOM.
    custom_time = models.TimeField(null=True, blank=True)
    skip_if_played = models.BooleanField(default=True)
    next_send_at = models.DateTimeField(db_index=True)
    # Edition number already reminded about: one reminder per edition.
    last_sent_number = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'напоминание о ежедневке'
        verbose_name_plural = 'напоминания о ежедневках'
        constraints = [
            models.UniqueConstraint(fields=['user', 'game_id'], name='games_dailyreminder_uniq'),
        ]

    def __str__(self):
        return 'DailyReminder {} {} {}'.format(self.user_id, self.game_id, self.mode)
