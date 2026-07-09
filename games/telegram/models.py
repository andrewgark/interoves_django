from django.db import models


class TelegramGameAnnouncement(models.Model):
    KIND_START = 'start'
    KIND_END_SOON_30 = 'end_soon_30'
    KIND_END = 'end'
    KIND_CHOICES = (
        (KIND_START, 'Game start'),
        (KIND_END_SOON_30, '30 minutes before end'),
        (KIND_END, 'Game end'),
    )

    game = models.ForeignKey('games.Game', related_name='telegram_announcements', on_delete=models.CASCADE)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['game', 'kind'], name='unique_telegram_game_announcement'),
        ]

    def __str__(self):
        return '{} [{}]'.format(self.game_id, self.kind)
