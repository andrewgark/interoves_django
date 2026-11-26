"""Криптик, предложенный игроком: черновик → на проверке → принят.

У черновика нет задания: подсказка, ответ и разбор лежат здесь, а решать его
можно по ссылке ``/cryptic/<share_hash>/`` (проверка без записи попыток).
Принятый криптик встаёт в конец расписания как обычный день, и ссылка
продолжает работать: день получает тот же ``share_hash``.
"""

from django.conf import settings
from django.db import models


class CrypticOffer(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_ACCEPTED = 'accepted'
    STATUS_VARIANTS = (
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_SENT, 'На проверке'),
        (STATUS_ACCEPTED, 'Принят'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='cryptic_offers', on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=16, choices=STATUS_VARIANTS, default=STATUS_DRAFT, db_index=True)
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    clue = models.TextField(blank=True, default='')
    answer = models.CharField(max_length=128, blank=True, default='')
    enumeration = models.CharField(max_length=64, blank=True, default='')
    markup = models.TextField(blank=True, default='')
    formula = models.TextField(blank=True, default='')
    comment = models.TextField(blank=True, default='', verbose_name='комментарий для редакторов')
    admin_note = models.TextField(blank=True, default='', verbose_name='заметка редактора при возврате')
    accepted_link = models.ForeignKey(
        'games.GameTaskGroup', related_name='accepted_cryptic_offers',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    # Кто из редакторов принял или вернул на доработку последним.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='reviewed_cryptic_offers',
        blank=True, null=True, on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        app_label = 'games'
        ordering = ['-updated_at']
        verbose_name = 'предложение криптика'
        verbose_name_plural = 'предложения криптиков'

    def __str__(self):
        return 'CrypticOffer #{} [{}] {}'.format(self.pk, self.status, self.answer or self.user_id)

    @property
    def status_label(self):
        if self.status == self.STATUS_DRAFT and self.sent_at:
            return 'На доработке'
        return dict(self.STATUS_VARIANTS).get(self.status, self.status)

    def can_author_edit(self) -> bool:
        return self.status == self.STATUS_DRAFT

    def play_url(self) -> str:
        return '/cryptic/{}/'.format(self.share_hash)
