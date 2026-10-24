"""Censorly offer. Same app and table as when it lived in games.models."""

from django.conf import settings
from django.db import models


class CensorlyOffer(models.Model):
    """Wikipedia article submitted by a player for the daily Censorly schedule."""

    STATUS_SENT = 'sent'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_VARIANTS = (
        (STATUS_SENT, 'Отправлена'),
        (STATUS_ACCEPTED, 'Принята'),
        (STATUS_REJECTED, 'Не выбрана'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='censorly_offers', on_delete=models.CASCADE,
    )
    party = models.ForeignKey(
        'games.RandomCensorlyGame', related_name='offers', on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=16, choices=STATUS_VARIANTS, default=STATUS_SENT, db_index=True)
    comment = models.TextField(blank=True, default='')
    admin_note = models.TextField(blank=True, default='')
    accepted_link = models.ForeignKey(
        'games.GameTaskGroup',
        related_name='accepted_censorly_offers',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        app_label = 'games'
        ordering = ['-updated_at']
        verbose_name = 'предложение Цензурки'
        verbose_name_plural = 'предложения Цензурок'

    @property
    def status_label(self):
        return dict(self.STATUS_VARIANTS).get(self.status, self.status)

    def __str__(self):
        return f'Цензурка #{self.pk} [{self.status}] {self.party.wiki_title}'
