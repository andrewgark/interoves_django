from __future__ import annotations

from django.db import models


class SocialQueuePost(models.Model):
    """Image + caption draft with independent publish status per network."""

    SOURCE_MANUAL = 'manual'
    SOURCE_LADDER = 'ladder'
    SOURCE_CHOICES = (
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_LADDER, 'Ladder'),
    )

    STATUS_PENDING = 'pending'
    STATUS_QUEUED = 'queued'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'

    TELEGRAM_STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_QUEUED, 'Queued (internal schedule)'),
        (STATUS_SCHEDULED, 'Scheduled in Telegram'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
    )
    NETWORK_STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_QUEUED, 'Queued (internal schedule)'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED, 'Skipped'),
    )

    caption = models.TextField(blank=True, default='')
    image = models.ImageField(upload_to='social_queue/', blank=True, null=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    ladder_date = models.DateField(
        null=True,
        blank=True,
        unique=True,
        help_text='MSK calendar date when source=ladder (idempotency key for cron)',
    )
    ladder_number = models.PositiveIntegerField(null=True, blank=True)
    play_url = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    telegram_status = models.CharField(
        max_length=16, choices=TELEGRAM_STATUS_CHOICES, default=STATUS_PENDING,
    )
    telegram_external_id = models.CharField(max_length=64, blank=True, default='')
    telegram_error = models.TextField(blank=True, default='')
    telegram_at = models.DateTimeField(null=True, blank=True)
    telegram_scheduled_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the post sits in Telegram native deferred messages',
    )
    telegram_queued_for = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Internal schedule: minute cron publishes to the channel at this time',
    )

    twitter_status = models.CharField(
        max_length=16, choices=NETWORK_STATUS_CHOICES, default=STATUS_PENDING,
    )
    twitter_external_id = models.CharField(max_length=64, blank=True, default='')
    twitter_error = models.TextField(blank=True, default='')
    twitter_at = models.DateTimeField(null=True, blank=True)
    twitter_queued_for = models.DateTimeField(null=True, blank=True)

    instagram_status = models.CharField(
        max_length=16, choices=NETWORK_STATUS_CHOICES, default=STATUS_PENDING,
    )
    instagram_external_id = models.CharField(max_length=64, blank=True, default='')
    instagram_error = models.TextField(blank=True, default='')
    instagram_at = models.DateTimeField(null=True, blank=True)
    instagram_queued_for = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        if self.source == self.SOURCE_LADDER and self.ladder_number:
            return 'ladder {} [tg={} x={} ig={}]'.format(
                self.ladder_number,
                self.telegram_status,
                self.twitter_status,
                self.instagram_status,
            )
        return 'social {} [tg={} x={} ig={}]'.format(
            self.pk,
            self.telegram_status,
            self.twitter_status,
            self.instagram_status,
        )

    def image_bytes(self) -> bytes:
        if not self.image:
            return b''
        self.image.open('rb')
        try:
            return self.image.read()
        finally:
            self.image.close()

    def set_image_bytes(self, data: bytes, filename: str = 'image.png') -> None:
        from django.core.files.base import ContentFile

        self.image.save(filename, ContentFile(data), save=False)

    @property
    def telegram_ok(self) -> bool:
        return self.telegram_status in (self.STATUS_SCHEDULED, self.STATUS_SENT)
