from django.db import models


class TelegramGameAnnouncement(models.Model):
    KIND_DAY_BEFORE = 'day_before'
    KIND_HOUR_BEFORE = 'hour_before'
    KIND_START = 'start'
    KIND_END_SOON_15 = 'end_soon_15'
    KIND_END_SOON_30 = 'end_soon_30'  # legacy; no longer sent
    KIND_END = 'end'
    KIND_RESULTS = 'results'
    KIND_CHOICES = (
        (KIND_DAY_BEFORE, 'Day before start'),
        (KIND_HOUR_BEFORE, 'Hour before start'),
        (KIND_START, 'Game start'),
        (KIND_END_SOON_15, '15 minutes before end'),
        (KIND_END_SOON_30, '30 minutes before end (legacy)'),
        (KIND_END, 'Game end'),
        (KIND_RESULTS, 'Tournament results after end'),
    )

    game = models.ForeignKey('games.Game', related_name='telegram_announcements', on_delete=models.CASCADE)
    # Fixed kinds above, plus dynamic ``all_solved:<team_id>``.
    kind = models.CharField(max_length=64)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['game', 'kind'], name='unique_telegram_game_announcement'),
        ]

    @staticmethod
    def all_solved_kind(team_id) -> str:
        return 'all_solved:{}'.format(team_id)

    def __str__(self):
        return '{} [{}]'.format(self.game_id, self.kind)


class TelegramLadderChannelPost(models.Model):
    """Daily ladder teaser scheduled into the channel via MTProto schedule_date."""

    STATUS_SCHEDULED = 'scheduled'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_SCHEDULED, 'Scheduled in Telegram'),
        (STATUS_SENT, 'Sent immediately'),
        (STATUS_FAILED, 'Failed'),
    )

    ladder_date = models.DateField(unique=True, help_text='MSK calendar date of the ladder')
    ladder_number = models.PositiveIntegerField()
    play_url = models.CharField(max_length=500)
    caption = models.TextField()
    image_png = models.BinaryField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    scheduled_for = models.DateTimeField(null=True, blank=True, help_text='Telegram schedule_date (usually 16:30 MSK)')
    prepared_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ('-ladder_date',)

    def __str__(self):
        return 'ladder {} [{}]'.format(self.ladder_number, self.status)
