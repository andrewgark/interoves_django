from django.db import models


class NextGameVoteGoalMapping(models.Model):
    """donation_request_id → candidate for the September 2026 next-game vote."""

    CANDIDATE_REDACTLE = 'redactle'
    CANDIDATE_CRYPTIC = 'cryptic'
    CANDIDATE_LOGIC = 'logic'
    CANDIDATE_CHOICES = (
        (CANDIDATE_REDACTLE, 'Redactle'),
        (CANDIDATE_CRYPTIC, 'Криптик'),
        (CANDIDATE_LOGIC, 'Логические пазлы'),
    )

    candidate = models.CharField(max_length=32, unique=True, choices=CANDIDATE_CHOICES)
    donation_request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    web_url = models.URLField(max_length=500, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'голос: цель Tribute'
        verbose_name_plural = 'голос: цели Tribute'
        ordering = ['candidate']

    def __str__(self):
        return '{} → {}'.format(self.get_candidate_display(), self.donation_request_id or '—')


class NextGameVoteEvent(models.Model):
    """One Tribute donation webhook (or ignored sibling event) for the next-game vote."""

    CANDIDATE_CHOICES = NextGameVoteGoalMapping.CANDIDATE_CHOICES

    RESULT_COUNTED = 'counted'
    RESULT_DUPLICATE = 'duplicate'
    RESULT_UNMAPPED = 'unmapped'
    RESULT_OUTSIDE_PERIOD = 'outside_period'
    RESULT_IGNORED_EVENT = 'ignored_event'
    RESULT_UNKNOWN_CURRENCY = 'unknown_currency'
    RESULT_MALFORMED = 'malformed'
    RESULT_CHOICES = (
        (RESULT_COUNTED, 'Учтён'),
        (RESULT_DUPLICATE, 'Повтор'),
        (RESULT_UNMAPPED, 'Без кандидата'),
        (RESULT_OUTSIDE_PERIOD, 'Вне периода'),
        (RESULT_IGNORED_EVENT, 'Игнорирован'),
        (RESULT_UNKNOWN_CURRENCY, 'Неизвестная валюта'),
        (RESULT_MALFORMED, 'Некорректный payload'),
    )

    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    event_name = models.CharField(max_length=64, db_index=True)
    donation_request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    tribute_donation_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    candidate = models.CharField(max_length=32, blank=True, default='', choices=CANDIDATE_CHOICES, db_index=True)
    original_amount_minor = models.BigIntegerField(blank=True, null=True)
    original_currency = models.CharField(max_length=3, blank=True, default='')
    fx_rate_to_eur = models.DecimalField(max_digits=20, decimal_places=10, blank=True, null=True)
    normalized_amount_eur_cents = models.BigIntegerField(default=0)
    donation_created_at = models.DateTimeField(blank=True, null=True, db_index=True)
    period = models.CharField(max_length=32, blank=True, default='')
    telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    trb_user_id = models.CharField(max_length=128, blank=True, default='')
    result = models.CharField(max_length=32, choices=RESULT_CHOICES, db_index=True)
    include_in_scoreboard = models.BooleanField(default=False, db_index=True)
    excluded = models.BooleanField(default=False, db_index=True)
    excluded_reason = models.TextField(blank=True, default='')
    excluded_by = models.ForeignKey(
        'auth.User',
        related_name='next_game_vote_exclusions',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    excluded_at = models.DateTimeField(blank=True, null=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    payload_excerpt = models.JSONField(default=dict, blank=True)
    matched_user = models.ForeignKey(
        'auth.User',
        related_name='next_game_vote_events',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    analytics_goal_queued_at = models.DateTimeField(blank=True, null=True)
    analytics_goal_sent_at = models.DateTimeField(blank=True, null=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'голос: Tribute webhook'
        verbose_name_plural = 'голос: Tribute webhooks'
        ordering = ['-received_at']

    def __str__(self):
        return 'Vote webhook {} ({})'.format(self.event_name, self.result)


class NextGameVoteAdjustment(models.Model):
    """Manual correction stored separately from Tribute payments."""

    CANDIDATE_CHOICES = NextGameVoteGoalMapping.CANDIDATE_CHOICES

    candidate = models.CharField(max_length=32, choices=CANDIDATE_CHOICES, db_index=True)
    amount_eur_cents = models.BigIntegerField(help_text='Корректировка в евроцентах; может быть отрицательной')
    comment = models.TextField()
    created_by = models.ForeignKey(
        'auth.User',
        related_name='next_game_vote_adjustments',
        blank=True,
        null=True,
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'голос: ручная корректировка'
        verbose_name_plural = 'голос: ручные корректировки'
        ordering = ['-created_at']

    def __str__(self):
        return '{} {:+d} ¢'.format(self.candidate, self.amount_eur_cents)


class NextGameVoteCampaignState(models.Model):
    """Singleton official snapshot for the September 2026 next-game vote."""

    FREEZE_DEADLINE = 'deadline'
    FREEZE_MANUAL = 'manual'
    FREEZE_CHOICES = (
        (FREEZE_DEADLINE, 'Дедлайн'),
        (FREEZE_MANUAL, 'Вручную'),
    )

    frozen_at = models.DateTimeField(blank=True, null=True)
    frozen_by = models.ForeignKey(
        'auth.User',
        related_name='next_game_vote_freezes',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    freeze_reason = models.CharField(max_length=16, blank=True, default='', choices=FREEZE_CHOICES)
    snapshot = models.JSONField(default=dict, blank=True)
    winner_slug = models.CharField(max_length=32, blank=True, default='')
    is_tie = models.BooleanField(default=False)
    mapping_blocker = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'голос: состояние кампании'
        verbose_name_plural = 'голос: состояние кампании'

    def __str__(self):
        if self.frozen_at:
            return 'Next-game vote frozen at {}'.format(self.frozen_at)
        return 'Next-game vote open'


class NextGameAutumnGoalMapping(models.Model):
    """donation_request_id → candidate for the October–November 2026 vote."""

    CANDIDATE_POWDERS = 'powders'
    CANDIDATE_CRYPTIC = 'cryptic'
    CANDIDATE_LOGIC = 'logic'
    CANDIDATE_CHOICES = (
        (CANDIDATE_POWDERS, 'Порошки'),
        (CANDIDATE_CRYPTIC, 'Криптики'),
        (CANDIDATE_LOGIC, 'Логички'),
    )

    candidate = models.CharField(max_length=32, unique=True, choices=CANDIDATE_CHOICES)
    donation_request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    web_url = models.URLField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'голос осени: цель Tribute'
        verbose_name_plural = 'голос осени: цели Tribute'
        ordering = ['candidate']

    def __str__(self):
        return '{} → {}'.format(self.get_candidate_display(), self.donation_request_id or '—')


class NextGameAutumnVoteEvent(models.Model):
    """One Tribute donation webhook for the October–November 2026 vote."""

    CANDIDATE_CHOICES = NextGameAutumnGoalMapping.CANDIDATE_CHOICES
    RESULT_COUNTED = 'counted'
    RESULT_OUTSIDE_PERIOD = 'outside_period'
    RESULT_IGNORED_EVENT = 'ignored_event'
    RESULT_UNKNOWN_CURRENCY = 'unknown_currency'
    RESULT_MALFORMED = 'malformed'
    RESULT_CHOICES = (
        (RESULT_COUNTED, 'Учтён'),
        (RESULT_OUTSIDE_PERIOD, 'Вне периода'),
        (RESULT_IGNORED_EVENT, 'Игнорирован'),
        (RESULT_UNKNOWN_CURRENCY, 'Неизвестная валюта'),
        (RESULT_MALFORMED, 'Некорректный payload'),
    )

    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    event_name = models.CharField(max_length=64, db_index=True)
    donation_request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    tribute_donation_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    candidate = models.CharField(max_length=32, blank=True, default='', choices=CANDIDATE_CHOICES, db_index=True)
    original_amount_minor = models.BigIntegerField(blank=True, null=True)
    original_currency = models.CharField(max_length=3, blank=True, default='')
    fx_rate_to_eur = models.DecimalField(max_digits=20, decimal_places=10, blank=True, null=True)
    normalized_amount_eur_cents = models.BigIntegerField(default=0)
    donation_created_at = models.DateTimeField(blank=True, null=True, db_index=True)
    period = models.CharField(max_length=32, blank=True, default='')
    telegram_user_id = models.BigIntegerField(blank=True, null=True)
    result = models.CharField(max_length=32, choices=RESULT_CHOICES, db_index=True)
    include_in_scoreboard = models.BooleanField(default=False, db_index=True)
    excluded = models.BooleanField(default=False, db_index=True)
    excluded_reason = models.TextField(blank=True, default='')
    raw_payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'голос осени: Tribute webhook'
        verbose_name_plural = 'голос осени: Tribute webhooks'
        ordering = ['-received_at']

    def __str__(self):
        return 'Autumn vote webhook {} ({})'.format(self.event_name, self.result)


class CensorlyReviewCase(models.Model):
    EXPECTED_OPEN = 'OPEN'
    EXPECTED_CLOSED = 'CLOSED'
    EXPECTED_CHOICES = (
        (EXPECTED_OPEN, 'Ожидалось открытие'),
        (EXPECTED_CLOSED, 'Ожидалось закрытие'),
    )
    STATUS_DIAGNOSED = 'DIAGNOSED'
    STATUS_NEEDS_REVIEW = 'NEEDS_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_APPLIED = 'APPLIED'
    STATUS_CHOICES = tuple((value, value) for value in (
        STATUS_DIAGNOSED, STATUS_NEEDS_REVIEW, STATUS_APPROVED,
        STATUS_REJECTED, STATUS_APPLIED,
    ))

    id = models.AutoField(primary_key=True)
    pair_key = models.CharField(max_length=260)
    guess = models.CharField(max_length=128)
    target = models.CharField(max_length=128)
    expected = models.CharField(max_length=6, choices=EXPECTED_CHOICES)
    actual = models.CharField(max_length=6, blank=True, default='')
    classification = models.CharField(max_length=128, blank=True, default='')
    fix_layer = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DIAGNOSED)
    decision = models.CharField(max_length=32, blank=True, default='')
    note = models.TextField(blank=True, default='')
    report_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pair_key', 'expected'),
                name='games_censorly_case_pair_expected_uniq',
            ),
        ]
        ordering = ['-updated_at']


class BugReport(models.Model):
    REPORT_TYPE_BUG = 'BUG'
    REPORT_TYPE_CENSORLY_WORD_MATCH = 'CENSORLY_WORD_MATCH'
    REPORT_TYPE_VARIANTS = (
        (REPORT_TYPE_BUG, 'Обычный баг-репорт'),
        (REPORT_TYPE_CENSORLY_WORD_MATCH, 'Матчинг слов Цензурки'),
    )
    STATUS_PENDING = 'Pending'
    STATUS_REVIEWED = 'Reviewed'
    STATUS_FIXED = 'Fixed'
    STATUS_DISMISSED = 'Dismissed'
    BUG_REPORT_STATUS_VARIANTS = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_REVIEWED, 'Reviewed'),
        (STATUS_FIXED, 'Fixed'),
        (STATUS_DISMISSED, 'Dismissed'),
    )
    STATUS_LABELS_RU = {
        STATUS_PENDING: 'На рассмотрении',
        STATUS_REVIEWED: 'Просмотрен',
        STATUS_FIXED: 'Исправлен',
        STATUS_DISMISSED: 'Отклонён',
    }
    USER_REPLY_STATUSES = (STATUS_PENDING, STATUS_REVIEWED)

    id = models.AutoField(primary_key=True)
    report_type = models.CharField(
        max_length=32,
        choices=REPORT_TYPE_VARIANTS,
        default=REPORT_TYPE_BUG,
        db_index=True,
    )
    task = models.ForeignKey('Task', related_name='bug_reports', on_delete=models.CASCADE)
    game = models.ForeignKey('Game', related_name='bug_reports', on_delete=models.CASCADE)
    team = models.ForeignKey('Team', related_name='bug_reports', blank=True, null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('auth.User', related_name='bug_reports', blank=True, null=True, on_delete=models.SET_NULL)
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    text = models.TextField()
    metadata = models.JSONField(blank=True, default=dict)
    censorly_review_case = models.ForeignKey(
        CensorlyReviewCase,
        related_name='reports',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    page_url = models.CharField(max_length=500, blank=True, default='')
    time = models.DateTimeField(auto_now_add=True, blank=True)

    status = models.CharField(default=STATUS_PENDING, max_length=100, choices=BUG_REPORT_STATUS_VARIANTS)
    admin_notes = models.TextField(blank=True, default='')
    user_last_read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-time']

    def __str__(self):
        excerpt = self.text.replace('\n', ' ')[:80]
        return '{}: [{}] {} — {}'.format(
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            self.status,
            self.task,
            excerpt,
        )

    def status_label_ru(self):
        return self.STATUS_LABELS_RU.get(self.status, self.status)

    def status_pill_class(self):
        if self.status in (self.STATUS_FIXED, self.STATUS_REVIEWED):
            return 'new-pill--ok'
        if self.status == self.STATUS_DISMISSED:
            return 'new-pill--wrong'
        return 'new-pill--partial'

    def user_can_reply(self):
        return self.status in self.USER_REPLY_STATUSES


class BugReportMessage(models.Model):
    ROLE_USER = 'user'
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = (
        (ROLE_USER, 'User'),
        (ROLE_ADMIN, 'Admin'),
    )

    id = models.AutoField(primary_key=True)
    report = models.ForeignKey(BugReport, related_name='messages', on_delete=models.CASCADE)
    author_user = models.ForeignKey(
        'auth.User',
        related_name='bug_report_messages',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    author_role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    telegram_message_id = models.BigIntegerField(blank=True, null=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        excerpt = self.text.replace('\n', ' ')[:60]
        return '#{} {} — {}'.format(self.report_id, self.author_role, excerpt)

    def is_opening(self):
        return not (
            BugReportMessage.objects
            .filter(report_id=self.report_id)
            .exclude(pk=self.pk)
            .exists()
        )


class PendingBugReport(BugReport):
    class Meta:
        proxy = True


class AlphabettyDictSuggestion(models.Model):
    """Предложение добавить слово в словарь Алфавитки (модерация в админке)."""

    STATUS_PENDING = 'Pending'
    STATUS_APPROVED = 'Approved'
    # Одобрено и для валидных отгадок, и для пула загадывания.
    STATUS_APPROVED_ANSWER = 'ApprovedAnswer'
    STATUS_REJECTED = 'Rejected'
    STATUS_VARIANTS = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_APPROVED_ANSWER, 'Approved for answer'),
        (STATUS_REJECTED, 'Rejected'),
    )
    # Статусы, при которых слово глобально принимается как отгадка.
    STATUSES_VALID = (STATUS_APPROVED, STATUS_APPROVED_ANSWER)

    id = models.AutoField(primary_key=True)
    word = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(
        default=STATUS_PENDING,
        max_length=32,
        choices=STATUS_VARIANTS,
        db_index=True,
    )
    suggest_count = models.PositiveIntegerField(default=1)
    user = models.ForeignKey(
        'auth.User',
        related_name='alphabetty_dict_suggestions',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    admin_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'предложение в словарь Алфавитки'
        verbose_name_plural = 'предложения в словарь Алфавитки'
    def __str__(self):
        return '[{}] {} ×{}'.format(self.status, self.word, self.suggest_count)


class PendingAlphabettyDictSuggestion(AlphabettyDictSuggestion):
    class Meta:
        proxy = True
        verbose_name = 'pending: словарь Алфавитки'
        verbose_name_plural = 'pending: словарь Алфавитки'


class AlphabettyPersonalDictWord(models.Model):
    """Личный словарь Алфавитки: слово валидно только для этого user/anon."""

    id = models.AutoField(primary_key=True)
    word = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(
        'auth.User',
        related_name='alphabetty_personal_dict_words',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    anon_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'личное слово Алфавитки'
        verbose_name_plural = 'личные слова Алфавитки'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'word'],
                condition=models.Q(user__isnull=False),
                name='ab_personal_dict_user_word_uniq',
            ),
            models.UniqueConstraint(
                fields=['anon_key', 'word'],
                condition=models.Q(anon_key__isnull=False),
                name='ab_personal_dict_anon_word_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'word'], name='ab_personal_dict_user_word_idx'),
            models.Index(fields=['anon_key', 'word'], name='ab_personal_dict_anon_word_idx'),
        ]

    def __str__(self):
        who = self.user_id or (self.anon_key[:8] if self.anon_key else '—')
        return '{} ({})'.format(self.word, who)


class OrderGameClient(models.Model):
    id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=200)
    logo_url = models.URLField(max_length=500)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'клиент (заказ игры)'
        verbose_name_plural = 'клиенты (заказ игры)'

    def __str__(self):
        return self.company_name


class OrderGameReview(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=120, verbose_name='имя')
    caption = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name='как подписать',
        help_text='Например: 31 годик, оператор ЭВМ',
    )
    photo_url = models.URLField(max_length=500, blank=True, default='')
    text = models.TextField()
    is_important = models.BooleanField(default=False, help_text='Важные отзывы показываются чуть чаще')
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_important', 'id']
        verbose_name = 'отзыв (заказ игры)'
        verbose_name_plural = 'отзывы (заказ игры)'

    def __str__(self):
        if self.caption:
            return '{}, {}'.format(self.name, self.caption)
        return self.name


class RandomAlphabettyGame(models.Model):
    """Permanent deduplicated alphabetty game created from a dictionary word."""

    word = models.CharField(max_length=64, unique=True)
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    task_group = models.OneToOneField(
        'TaskGroup',
        related_name='random_alphabetty_game',
        on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Случайная алфавитка #{self.share_hash}: {self.word}'


class RandomCensorlyGame(models.Model):
    """Permanent deduplicated Цензурка from a Russian Wikipedia title."""

    wiki_title = models.CharField(max_length=255, unique=True)
    share_hash = models.CharField(max_length=32, unique=True, db_index=True)
    task_group = models.OneToOneField(
        'TaskGroup',
        related_name='random_censorly_game',
        on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Случайная цензурка #{self.share_hash}: {self.wiki_title}'

    def play_url(self):
        return '/censorly/r/{}/'.format(self.share_hash)


class CensorlyCategory(models.Model):
    """Editable node in the Censorly topic tree."""

    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        'self', related_name='children', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    parent_key = models.BigIntegerField(default=0, editable=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name', 'pk']
        constraints = [models.UniqueConstraint(
            fields=('parent_key', 'name'), name='uniq_censorly_category_sibling_name',
        )]

    def save(self, *args, **kwargs):
        self.parent_key = self.parent_id or 0
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'parent_key'}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_ancestors_ids(self):
        ids = set()
        parent = self.parent
        while parent is not None:
            ids.add(parent.pk)
            parent = parent.parent
        return ids


class CensorlyObjectType(models.Model):
    """Editable flat object-type vocabulary for Censorly pool entries."""

    name = models.CharField(max_length=120, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name', 'pk']

    def __str__(self):
        return self.name


class CensorlyPoolEntry(models.Model):
    """Database overrides and additions for the curated Censorly article pool."""

    title = models.CharField(max_length=255, unique=True)
    categories = models.ManyToManyField(CensorlyCategory, related_name='pool_entries', blank=True)
    types = models.ManyToManyField(CensorlyObjectType, related_name='pool_entries', blank=True)
    # Kept through the transition so the imported source can be audited/exported.
    topics = models.JSONField(default=list, blank=True)
    object_types = models.JSONField(default=list, blank=True)
    difficulty = models.CharField(max_length=16, blank=True, default='')
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title


class CorporateGameOrder(models.Model):
    class ContactMethod(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram'
        EMAIL = 'email', 'Email'
        OTHER = 'other', 'Другое'

    id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200)
    contact_method = models.CharField(
        max_length=20,
        choices=ContactMethod.choices,
        default=ContactMethod.TELEGRAM,
        verbose_name='способ связи',
    )
    contact_value = models.CharField(max_length=254, verbose_name='контакт')
    contact_other_label = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='тип контакта',
        help_text='Для «Другое»: WhatsApp, VK и т.п.',
    )
    team_size = models.CharField(max_length=100, blank=True, default='')
    preferred_date = models.CharField(max_length=200, blank=True, default='')
    message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    email_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'заявка на корпоративную игру'
        verbose_name_plural = 'заявки на корпоративные игры'

    def __str__(self):
        return '{} — {} ({})'.format(self.company_name, self.contact_name, self.created_at.strftime('%Y-%m-%d %H:%M'))
