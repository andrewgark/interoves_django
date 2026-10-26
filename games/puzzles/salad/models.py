"""Salad offer. Same app and table as when it lived in games.models."""

from django.conf import settings
from django.db import models


class WordSaladOffer(models.Model):
    """Предложение салатика: идея без сетки или полный играбельный пазл."""

    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_ACCEPTED = 'accepted'
    STATUS_VARIANTS = (
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SENT, 'Отправлена'),
        (STATUS_ACCEPTED, 'Принята'),
    )

    KIND_IDEA = 'idea'
    KIND_FULL = 'full'
    KIND_VARIANTS = (
        (KIND_IDEA, 'Идея'),
        (KIND_FULL, 'Салатик'),
    )

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='word_salad_offers',
        on_delete=models.CASCADE,
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND_VARIANTS,
        default=KIND_FULL,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_VARIANTS,
        default=STATUS_DRAFT,
        db_index=True,
    )
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    theme = models.TextField(blank=True, default='')
    author = models.CharField(max_length=200, blank=True, default='')
    idea_text = models.TextField(blank=True, default='')
    suggested_words = models.TextField(blank=True, default='')
    grid_text = models.TextField(blank=True, default='')
    words_text = models.TextField(blank=True, default='')
    rare_words_text = models.TextField(blank=True, default='')
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
        related_name='word_salad_offer',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    accepted_link = models.ForeignKey(
        'games.GameTaskGroup',
        related_name='accepted_word_salad_offers',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    converted_from = models.ForeignKey(
        'self',
        related_name='converted_offers',
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
        verbose_name = 'предложение салатика'
        verbose_name_plural = 'предложения салатиков'

    def __str__(self):
        return 'WordSaladOffer #{} [{}] {}'.format(
            self.pk, self.status, self.theme or self.user_id,
        )

    @property
    def status_label(self):
        if self.status == self.STATUS_DRAFT and (self.sent_at or self.accepted_at):
            return 'На доработке'
        return dict(self.STATUS_VARIANTS).get(self.status, self.status)

    @property
    def kind_label(self):
        return dict(self.KIND_VARIANTS).get(self.kind, self.kind)

    def can_author_edit(self):
        if self.status != self.STATUS_DRAFT:
            return False
        if self.accepted_link_id:
            try:
                number = int(self.accepted_link.number)
            except (TypeError, ValueError, AttributeError):
                number = None
            if number is not None:
                from games.daily.catalog.word_salad import is_word_salad_number_published
                from games.models import Game
                game = Game.objects.filter(pk='salad').first()
                if game is not None and is_word_salad_number_published(game, number):
                    return False
        return True

    def play_url(self):
        if self.kind != self.KIND_FULL or not self.share_hash:
            return ''
        return '/salad/{}/'.format(self.share_hash)
