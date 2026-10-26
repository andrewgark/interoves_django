import secrets

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

RECENT_TICKET_REQUESTS_LIMIT = 10
TICKET_REQUESTS_PAGE_SIZE = 10


class TicketRequest(models.Model):
    PROVIDER_MANUAL = 'manual'
    PROVIDER_YOOKASSA = 'yookassa'
    PROVIDER_NOWPAYMENTS = 'nowpayments'
    PROVIDER_TRIBUTE = 'tribute'
    PROVIDER_TRIBUTE_DIGITAL = 'tribute_digital'
    PROVIDER_VPOS = 'vpos'
    PAYMENT_PROVIDER_CHOICES = (
        (PROVIDER_MANUAL, 'Manual / legacy'),
        (PROVIDER_YOOKASSA, 'YooKassa'),
        (PROVIDER_NOWPAYMENTS, 'NOWPayments'),
        (PROVIDER_TRIBUTE, 'Tribute (legacy)'),
        (PROVIDER_TRIBUTE_DIGITAL, 'Tribute Digital Product'),
        (PROVIDER_VPOS, 'Armenian acquiring / VPOS'),
    )

    MERCHANT_RU = 'ru_self_employed'
    MERCHANT_AM = 'am_ie'
    MERCHANT_LEGACY_UNSPECIFIED = 'legacy_unspecified'
    MERCHANT_CHOICES = (
        (MERCHANT_RU, 'Андрей Гаркавый, плательщик НПД, РФ'),
        (MERCHANT_AM, 'Andrei Garkavyi IE, Republic of Armenia'),
        (MERCHANT_LEGACY_UNSPECIFIED, 'Legacy / unspecified'),
    )

    CURRENCY_RUB = 'RUB'
    CURRENCY_AMD = 'AMD'
    CURRENCY_EUR = 'EUR'
    CURRENCY_CHOICES = (
        (CURRENCY_RUB, 'RUB'),
        (CURRENCY_AMD, 'AMD'),
        (CURRENCY_EUR, 'EUR'),
    )

    id = models.AutoField(primary_key=True)
    team = models.ForeignKey('Team', related_name='ticket_requests', blank=True, null=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(
        'auth.User',
        related_name='created_ticket_requests',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    metrika_client_id = models.CharField(max_length=64, blank=True, default='')
    money = models.DecimalField(
        default=0,
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    tickets = models.IntegerField(default=0, validators=[MinValueValidator(1),MaxValueValidator(20)])
    time = models.DateTimeField(auto_now_add=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=CURRENCY_RUB)
    payment_provider = models.CharField(
        max_length=32,
        choices=PAYMENT_PROVIDER_CHOICES,
        default=PROVIDER_MANUAL,
    )
    merchant = models.CharField(max_length=32, choices=MERCHANT_CHOICES, default=MERCHANT_RU)
    yookassa_id = models.TextField(null=True, blank=True)
    nowpayments_id = models.TextField(null=True, blank=True)
    tribute_id = models.TextField(null=True, blank=True)
    checkout_goal_acked_at = models.DateTimeField(null=True, blank=True)
    purchase_goal_queued_at = models.DateTimeField(null=True, blank=True, db_index=True)
    purchase_goal_sent_at = models.DateTimeField(null=True, blank=True)

    TICKER_REQUEST_STATUS_VARIANTS = (
        ('Pending', 'Pending'),
        ('Accepted', 'Accepted'),
        ('Rejected', 'Rejected'),
    )

    status = models.CharField(default='Pending', max_length=100, choices=TICKER_REQUEST_STATUS_VARIANTS)

    def __str__(self):
        return '{}: [{}] Сумма: {} {} - Билетов: {} - Команда: {}'.format(
            self.time.strftime('%Y-%m-%d %H:%M:%S'),
            self.status,
            self.money,
            self.currency,
            self.tickets,
            self.team
        )

    @classmethod
    def recent_for_team(cls, team, limit=None):
        if not team:
            return []
        n = RECENT_TICKET_REQUESTS_LIMIT if limit is None else limit
        return list(cls.objects.filter(team=team).order_by('-time')[:n])


class PendingTicketRequest(TicketRequest):
    class Meta:
        proxy=True


class TributePaymentIntent(models.Model):
    TYPE_REGULAR = 'regular'
    TYPE_DISCOUNT = 'discount'
    TICKET_TYPE_CHOICES = (
        (TYPE_REGULAR, 'Regular'),
        (TYPE_DISCOUNT, 'School / student'),
    )

    STATUS_AWAITING = 'awaiting_payment'
    STATUS_COMPLETED = 'completed'
    STATUS_EXPIRED = 'expired'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_AWAITING, 'Awaiting payment'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_CANCELLED, 'Cancelled'),
    )

    user = models.ForeignKey('auth.User', related_name='tribute_payment_intents', on_delete=models.PROTECT)
    team = models.ForeignKey('Team', related_name='tribute_payment_intents', on_delete=models.PROTECT)
    ticket_request = models.OneToOneField(
        TicketRequest,
        related_name='tribute_payment_intent',
        on_delete=models.PROTECT,
    )
    telegram_user_id = models.BigIntegerField(db_index=True)
    expected_product_id = models.BigIntegerField(db_index=True)
    expected_amount = models.BigIntegerField(help_text='Smallest currency units')
    expected_currency = models.CharField(max_length=3)
    ticket_type = models.CharField(max_length=16, choices=TICKET_TYPE_CHOICES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_AWAITING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=('telegram_user_id', 'expected_product_id'),
                condition=models.Q(status='awaiting_payment'),
                name='uniq_active_tribute_intent_identity_product',
            ),
        ]

    def __str__(self):
        return 'Tribute intent #{} ({})'.format(self.pk, self.status)


class TributePurchase(models.Model):
    STATUS_MANUAL_REVIEW = 'manual_review'
    STATUS_ISSUED = 'issued'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = (
        (STATUS_MANUAL_REVIEW, 'Manual review'),
        (STATUS_ISSUED, 'Ticket issued'),
        (STATUS_REFUNDED, 'Refunded'),
    )

    REASON_MISSING_TELEGRAM = 'missing_telegram_identity'
    REASON_UNKNOWN_TELEGRAM = 'unknown_telegram_identity'
    REASON_UNKNOWN_PRODUCT = 'unknown_product'
    REASON_AMOUNT_MISMATCH = 'invalid_amount'
    REASON_CURRENCY_MISMATCH = 'invalid_currency'
    REASON_NO_INTENT = 'no_active_intent'
    REASON_MULTIPLE_INTENTS = 'multiple_active_intents'
    REASON_DISCOUNT_INELIGIBLE = 'discount_ineligible'
    REASON_ORIGINAL_NOT_FOUND = 'original_purchase_not_found'
    REASON_TICKET_USED = 'ticket_already_used'

    purchase_id = models.CharField(max_length=128, unique=True, db_index=True)
    transaction_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    product_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    product_name = models.CharField(max_length=255, blank=True, default='')
    amount = models.BigIntegerField(blank=True, null=True, help_text='Smallest currency units')
    currency = models.CharField(max_length=3, blank=True, default='')
    trb_user_id = models.CharField(max_length=128, blank=True, default='')
    telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    telegram_username = models.CharField(max_length=64, blank=True, default='')
    purchase_created_at = models.DateTimeField(blank=True, null=True)
    normalized_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_MANUAL_REVIEW, db_index=True)
    review_reason = models.CharField(max_length=64, blank=True, default='', db_index=True)
    matched_user = models.ForeignKey(
        'auth.User', related_name='tribute_purchases', blank=True, null=True, on_delete=models.PROTECT,
    )
    matched_team = models.ForeignKey(
        'Team', related_name='tribute_purchases', blank=True, null=True, on_delete=models.PROTECT,
    )
    payment_intent = models.OneToOneField(
        TributePaymentIntent,
        related_name='purchase',
        blank=True,
        null=True,
        on_delete=models.PROTECT,
    )
    ticket_request = models.OneToOneField(
        TicketRequest,
        related_name='tribute_purchase',
        blank=True,
        null=True,
        on_delete=models.PROTECT,
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    refunded_at = models.DateTimeField(blank=True, null=True)
    refund_reason = models.CharField(max_length=255, blank=True, default='')
    ticket_revoked_at = models.DateTimeField(blank=True, null=True)
    accounting_review_required = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return 'Tribute purchase {} ({})'.format(self.purchase_id, self.status)


class SavedPaymentMethod(models.Model):
    """Minimal reusable credential; detachment permanently erases the token."""

    user = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='saved_payment_methods')
    provider = models.CharField(max_length=16, default='yookassa')
    provider_payment_method_id = models.CharField(max_length=64, null=True, blank=True)
    method_type = models.CharField(max_length=32, default='bank_card')
    card_last4 = models.CharField(max_length=4, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    detached_at = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self):
        if self.method_type != 'bank_card':
            return 'Сохранённый способ оплаты'
        return 'Банковская карта' + (' •••• ' + self.card_last4 if self.card_last4 else '')

    def __str__(self):
        return 'Saved payment method {} user={}'.format(self.pk, self.user_id)


class ClubSubscription(models.Model):
    """Canonical Inter Oves Club access (Tribute and/or YooKassa billing)."""

    PROVIDER_TRIBUTE = 'tribute'
    PROVIDER_YOOKASSA = 'yookassa'
    PROVIDER_CHOICES = (
        (PROVIDER_TRIBUTE, 'Tribute'),
        (PROVIDER_YOOKASSA, 'YooKassa'),
    )

    PLAN_MONTHLY = 'monthly'
    PLAN_ANNUAL = 'annual'
    PLAN_CHOICES = (
        ('', 'Unspecified'),
        (PLAN_MONTHLY, 'Monthly'),
        (PLAN_ANNUAL, 'Annual'),
    )

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'
    STATUS_PAST_DUE = 'past_due'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_PAST_DUE, 'Past due'),
    )

    user = models.OneToOneField(
        'auth.User',
        related_name='club_subscription',
        on_delete=models.PROTECT,
    )
    provider = models.CharField(
        max_length=16,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_TRIBUTE,
        db_index=True,
    )
    plan = models.CharField(
        max_length=16,
        choices=PLAN_CHOICES,
        blank=True,
        default='',
        db_index=True,
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_EXPIRED,
        db_index=True,
    )
    tribute_subscription_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    tribute_period_id = models.BigIntegerField(blank=True, null=True)
    telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    saved_payment_method = models.ForeignKey(
        SavedPaymentMethod, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='subscriptions',
    )
    payment_method_detached_at = models.DateTimeField(null=True, blank=True)
    payment_method_save_failed = models.BooleanField(default=False)
    intro_offer_used_at = models.DateTimeField(blank=True, null=True)
    current_period_start = models.DateTimeField(blank=True, null=True)
    next_charge_at = models.DateTimeField(blank=True, null=True, db_index=True)
    currency = models.CharField(max_length=3, blank=True, default='')
    amount = models.BigIntegerField(blank=True, null=True, help_text='Smallest currency units')
    auto_renew = models.BooleanField(default=False)
    paid_until = models.DateTimeField(blank=True, null=True, db_index=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    last_payment_at = models.DateTimeField(blank=True, null=True)
    last_webhook_at = models.DateTimeField(blank=True, null=True)
    last_webhook_event = models.CharField(max_length=64, blank=True, default='')
    last_event_created_at = models.DateTimeField(blank=True, null=True)
    duplicate_detected = models.BooleanField(default=False, db_index=True)
    payment_success_goal_queued_at = models.DateTimeField(blank=True, null=True)
    payment_success_goal_sent_at = models.DateTimeField(blank=True, null=True)
    renewal_goal_queued_at = models.DateTimeField(blank=True, null=True)
    renewal_goal_sent_at = models.DateTimeField(blank=True, null=True)
    cancelled_goal_queued_at = models.DateTimeField(blank=True, null=True)
    cancelled_goal_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return 'Club subscription user={} ({})'.format(self.user_id, self.status)

    def grants_access(self, now=None) -> bool:
        from django.utils import timezone as dj_timezone

        now = now or dj_timezone.now()
        if self.paid_until and self.paid_until > now:
            return True
        return self.entitlements.filter(
            kind__in=(ClubEntitlement.KIND_GIFT, ClubEntitlement.KIND_MANUAL),
            revoked_at__isnull=True,
            starts_at__lte=now,
        ).filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now)).exists()

    def effective_status(self, now=None) -> str:
        if not self.grants_access(now):
            if self.status == self.STATUS_PENDING and not self.paid_until:
                return self.STATUS_PENDING
            if (
                self.provider == self.PROVIDER_YOOKASSA
                and self.auto_renew
                and self.paid_until
                and self.status == self.STATUS_PAST_DUE
            ):
                return self.STATUS_PAST_DUE
            return self.STATUS_EXPIRED
        if self.auto_renew:
            return self.STATUS_ACTIVE
        return self.STATUS_CANCELLED


class SubscriptionGift(models.Model):
    """A one-time subscription gift created by an administrator or paid user."""

    STATUS_CREATED = 'created'
    STATUS_PAID = 'paid'
    STATUS_CLAIMED = 'claimed'
    STATUS_REVOKED = 'revoked'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = (
        (STATUS_CREATED, 'Created'),
        (STATUS_PAID, 'Paid'),
        (STATUS_CLAIMED, 'Claimed'),
        (STATUS_REVOKED, 'Revoked'),
        (STATUS_EXPIRED, 'Expired'),
    )

    PROVIDER_YOOKASSA = 'yookassa'
    PROVIDER_TRIBUTE = 'tribute'
    PROVIDER_CHOICES = (
        (PROVIDER_YOOKASSA, 'YooKassa'),
        (PROVIDER_TRIBUTE, 'Tribute'),
    )

    recipient_telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    recipient_telegram_username = models.CharField(max_length=64, blank=True, default='')
    code_hash = models.CharField(max_length=64, unique=True, db_index=True)
    duration_months = models.PositiveIntegerField(blank=True, null=True)
    is_forever = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_CREATED, db_index=True)
    created_by = models.ForeignKey(
        'auth.User', related_name='created_subscription_gifts', on_delete=models.PROTECT,
    )
    created_by_telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    purchaser = models.ForeignKey(
        'auth.User', related_name='purchased_subscription_gifts', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, blank=True, default='')
    code_ciphertext = models.TextField(blank=True, default='')
    paid_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    claimed_by = models.ForeignKey(
        'auth.User', related_name='claimed_subscription_gifts', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    claimed_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    telegram_message_id = models.BigIntegerField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=(models.Q(is_forever=True, duration_months__isnull=True)
                       | models.Q(is_forever=False, duration_months__isnull=False)),
                name='subscription_gift_duration_valid',
            ),
        ]

    def __str__(self):
        duration = 'навсегда' if self.is_forever else '{} мес.'.format(self.duration_months)
        return 'Подарок #{} ({})'.format(self.pk, duration)


class ClubEntitlement(models.Model):
    """Immutable-ish ledger entry granting Club access for a concrete period."""

    KIND_PAID = 'paid'
    KIND_GIFT = 'gift'
    KIND_MANUAL = 'manual'
    KIND_CHOICES = (
        (KIND_PAID, 'Paid'),
        (KIND_GIFT, 'Gift'),
        (KIND_MANUAL, 'Manual'),
    )

    user = models.ForeignKey(
        'auth.User', related_name='club_entitlements', on_delete=models.PROTECT,
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(blank=True, null=True, db_index=True)
    gift = models.ForeignKey(
        SubscriptionGift, related_name='entitlements', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    club_subscription = models.ForeignKey(
        ClubSubscription, related_name='entitlements', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    yookassa_payment = models.ForeignKey(
        'ClubYooKassaPayment', related_name='entitlements', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    tribute_event = models.ForeignKey(
        'ClubSubscriptionEvent', related_name='entitlements', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    created_by = models.ForeignKey(
        'auth.User', related_name='created_club_entitlements', blank=True, null=True,
        on_delete=models.PROTECT,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-starts_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=('yookassa_payment',),
                condition=models.Q(yookassa_payment__isnull=False),
                name='club_entitlement_yookassa_payment_uniq',
            ),
            models.UniqueConstraint(
                fields=('tribute_event',),
                condition=models.Q(tribute_event__isnull=False),
                name='club_entitlement_tribute_event_uniq',
            ),
            models.CheckConstraint(
                check=models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F('starts_at')),
                name='club_entitlement_period_valid',
            ),
        ]

    def __str__(self):
        return 'Club access user={} {}—{}'.format(self.user_id, self.starts_at, self.ends_at or 'forever')


class SubscriptionGiftPayment(models.Model):
    """A one-time payment attempt that creates a paid subscription gift."""

    PROVIDER_YOOKASSA = SubscriptionGift.PROVIDER_YOOKASSA
    PROVIDER_TRIBUTE = SubscriptionGift.PROVIDER_TRIBUTE
    PROVIDER_CHOICES = SubscriptionGift.PROVIDER_CHOICES
    STATUS_PENDING = 'pending'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_CANCELED = 'canceled'
    STATUS_MANUAL_REVIEW = 'manual_review'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCEEDED, 'Succeeded'),
        (STATUS_CANCELED, 'Canceled'),
        (STATUS_MANUAL_REVIEW, 'Manual review'),
    )

    gift = models.OneToOneField(
        SubscriptionGift, related_name='payment', on_delete=models.PROTECT,
    )
    purchaser = models.ForeignKey(
        'auth.User', related_name='subscription_gift_payments', on_delete=models.PROTECT,
    )
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES)
    provider_payment_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    expected_amount = models.PositiveIntegerField(help_text='Smallest currency units')
    currency = models.CharField(max_length=3)
    duration_months = models.PositiveIntegerField()
    telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    confirmation_url = models.URLField(max_length=500, blank=True, default='')
    purchase_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    raw_event_excerpt = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    succeeded_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('provider', 'provider_payment_id'),
                condition=~models.Q(provider_payment_id=''),
                name='subscription_gift_payment_provider_id_uniq',
            ),
            models.UniqueConstraint(
                fields=('provider', 'purchase_id'),
                condition=~models.Q(purchase_id=''),
                name='subscription_gift_payment_purchase_id_uniq',
            ),
        ]

    def __str__(self):
        return 'Gift payment {} ({})'.format(self.pk, self.status)


class ClubYooKassaPayment(models.Model):
    """One YooKassa payment attempt for Club billing (initial, renewal, or annual)."""

    KIND_INITIAL_MONTHLY = 'initial_monthly'
    KIND_RECURRING_MONTHLY = 'recurring_monthly'
    KIND_ANNUAL = 'annual'
    KIND_CHOICES = (
        (KIND_INITIAL_MONTHLY, 'Initial monthly'),
        (KIND_RECURRING_MONTHLY, 'Recurring monthly'),
        (KIND_ANNUAL, 'Annual'),
    )

    STATUS_PENDING = 'pending'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_CANCELED = 'canceled'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCEEDED, 'Succeeded'),
        (STATUS_CANCELED, 'Canceled'),
    )

    club_subscription = models.ForeignKey(
        ClubSubscription,
        related_name='yookassa_payments',
        on_delete=models.PROTECT,
    )
    user = models.ForeignKey(
        'auth.User',
        related_name='club_yookassa_payments',
        on_delete=models.PROTECT,
    )
    yookassa_payment_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    period_start = models.DateTimeField(blank=True, null=True)
    period_end = models.DateTimeField(blank=True, null=True)
    period_key = models.CharField(
        max_length=64,
        help_text='Stable billing-period key for uniqueness, e.g. 2026-10-15:recurring_monthly',
    )
    amount = models.PositiveIntegerField(help_text='Kopecks')
    currency = models.CharField(max_length=3, default='RUB')
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=64, unique=True)
    confirmation_url = models.URLField(max_length=500, blank=True, default='')
    failure_code = models.CharField(max_length=64, blank=True, default='')
    cancellation_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    succeeded_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['yookassa_payment_id'],
                condition=~models.Q(yookassa_payment_id=''),
                name='club_yk_payment_yookassa_id_uniq',
            ),
            models.UniqueConstraint(
                fields=['club_subscription', 'period_key'],
                name='club_yk_payment_period_key_uniq',
            ),
        ]

    def __str__(self):
        return 'ClubYooKassaPayment {} {} ({})'.format(self.pk, self.kind, self.status)


class ClubSubscriptionEvent(models.Model):
    """Idempotent Tribute subscription webhook audit trail."""

    RESULT_APPLIED = 'applied'
    RESULT_DUPLICATE = 'duplicate'
    RESULT_IGNORED_PRODUCT = 'ignored_unknown_product'
    RESULT_UNMATCHED_TELEGRAM = 'unmatched_telegram'
    RESULT_MALFORMED = 'malformed'
    RESULT_IGNORED_TYPE = 'ignored_type'
    RESULT_DELAYED = 'delayed'
    RESULT_ANOMALY = 'anomaly'
    RESULT_CHOICES = (
        (RESULT_APPLIED, 'Applied'),
        (RESULT_DUPLICATE, 'Duplicate'),
        (RESULT_IGNORED_PRODUCT, 'Unknown product'),
        (RESULT_UNMATCHED_TELEGRAM, 'Unknown Telegram'),
        (RESULT_MALFORMED, 'Malformed'),
        (RESULT_IGNORED_TYPE, 'Ignored type'),
        (RESULT_DELAYED, 'Delayed'),
        (RESULT_ANOMALY, 'Anomaly'),
    )

    event_key = models.CharField(max_length=255, unique=True, db_index=True)
    event_name = models.CharField(max_length=64, db_index=True)
    club_subscription = models.ForeignKey(
        ClubSubscription,
        related_name='events',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    tribute_subscription_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    tribute_period_id = models.BigIntegerField(blank=True, null=True)
    telegram_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    result = models.CharField(max_length=32, choices=RESULT_CHOICES, db_index=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    payload_excerpt = models.JSONField(blank=True, default=dict)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return 'Club webhook {} ({})'.format(self.event_name, self.result)


def _donation_public_token():
    import secrets
    return secrets.token_urlsafe(32)


class Donation(models.Model):
    """Crypto (NOWPayments) donation tracked from invoice create through IPN confirm."""

    id = models.AutoField(primary_key=True)
    amount_rub = models.IntegerField(validators=[MinValueValidator(1)])
    pay_amount = models.CharField(max_length=64, blank=True, default='')
    pay_currency = models.CharField(max_length=16, blank=True, default='')
    nowpayments_id = models.TextField(null=True, blank=True)
    public_token = models.CharField(max_length=64, unique=True, default=_donation_public_token, db_index=True)
    user = models.ForeignKey(
        'auth.User',
        related_name='donations',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    DONATION_STATUS_VARIANTS = (
        ('Pending', 'Pending'),
        ('Confirmed', 'Confirmed'),
        ('Rejected', 'Rejected'),
    )
    status = models.CharField(default='Pending', max_length=100, choices=DONATION_STATUS_VARIANTS)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        pay = ''
        if self.pay_amount and self.pay_currency:
            pay = ' ({} {})'.format(self.pay_amount, self.pay_currency)
        return '{}: [{}] {} ₽{}'.format(
            self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '—',
            self.status,
            self.amount_rub,
            pay,
        )

    def order_id(self):
        return 'donation-{}'.format(self.pk)
