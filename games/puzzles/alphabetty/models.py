"""Alphabetty offer. Same app and table as when it lived in games.models."""

from django.conf import settings
from django.db import models


class AlphabettyOffer(models.Model):
    """Предложение алфавитки от пользователя (черновик -> отправлена -> принята)."""

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
        related_name='alphabetty_offers',
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_VARIANTS,
        default=STATUS_DRAFT,
        db_index=True,
    )
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    word = models.CharField(max_length=64, blank=True, default='')
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
    task_group = models.OneToOneField(
        'games.TaskGroup',
        related_name='alphabetty_offer',
        on_delete=models.CASCADE,
    )
    accepted_link = models.ForeignKey(
        'games.GameTaskGroup',
        related_name='accepted_alphabetty_offers',
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
        verbose_name = 'предложение алфавитки'
        verbose_name_plural = 'предложения алфавиток'

    def __str__(self):
        return 'AlphabettyOffer #{} [{}] {}'.format(
            self.pk, self.status, self.word or self.user_id,
        )

    @property
    def status_label(self):
        if self.status == self.STATUS_DRAFT and (self.sent_at or self.accepted_at):
            return 'На доработке'
        return dict(self.STATUS_VARIANTS).get(self.status, self.status)

    def can_author_edit(self):
        if self.status != self.STATUS_DRAFT:
            return False
        if self.accepted_link_id:
            try:
                number = int(self.accepted_link.number)
            except (TypeError, ValueError, AttributeError):
                number = None
            if number is not None:
                from games.alphabetty_daily import is_alphabetty_number_published
                from games.models import Game
                game = Game.objects.filter(pk='alphabetty').first()
                if game is not None and is_alphabetty_number_published(game, number):
                    return False
        return True

    def play_url(self):
        return '/alphabetty/{}/'.format(self.share_hash)
