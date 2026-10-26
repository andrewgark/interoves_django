from django.contrib import admin, messages
from django.forms import Textarea
from django.db import models
from games.ops.workers.jobs.actions import (
    accept_ticket,
    reject_ticket,
)
from games.models import (
    Donation,
    PendingTicketRequest,
    TicketRequest,
    TributePaymentIntent,
    TributePurchase,
    ClubSubscription,
    ClubSubscriptionEvent,
    ClubYooKassaPayment,
    ClubEntitlement,
    SubscriptionGift,
    SubscriptionGiftPayment,
)


@admin.register(TicketRequest)
class TicketRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'team', 'created_by', 'tickets', 'money', 'currency', 'payment_provider', 'merchant',
        'status', 'time', 'checkout_goal_acked_at', 'purchase_goal_queued_at', 'purchase_goal_sent_at',
    )
    list_filter = ('status', 'currency', 'payment_provider', 'merchant')
    search_fields = (
        'id', 'team__name', 'team__visible_name', 'metrika_client_id',
        'yookassa_id', 'nowpayments_id', 'tribute_id',
    )
    readonly_fields = (
        'time', 'metrika_client_id', 'checkout_goal_acked_at',
        'purchase_goal_queued_at', 'purchase_goal_sent_at',
    )
    raw_id_fields = ('created_by', 'team')


@admin.action(description='Issue one ticket for selected reviewed Tribute purchases')
def issue_tribute_purchase(modeladmin, request, queryset):
    from games.tribute_service import TributeCheckoutError, manually_issue_purchase

    issued = 0
    for purchase in queryset:
        try:
            result = manually_issue_purchase(purchase.pk)
        except TributeCheckoutError as exc:
            modeladmin.message_user(
                request,
                '{}: {}'.format(purchase.purchase_id, exc.message),
                level=messages.ERROR,
            )
            continue
        if result.ticket_issued:
            issued += 1
    if issued:
        modeladmin.message_user(request, 'Начислено билетов: {}'.format(issued), level=messages.SUCCESS)


@admin.register(TributePurchase)
class TributePurchaseAdmin(admin.ModelAdmin):
    list_display = (
        'received_at', 'status', 'product_id', 'amount', 'currency', 'purchase_id',
        'transaction_id', 'telegram_user_id', 'telegram_username', 'matched_user',
        'matched_team', 'review_reason', 'accounting_review_required',
    )
    list_filter = (
        'status', 'review_reason', 'currency', 'product_id', 'accounting_review_required',
    )
    search_fields = (
        'purchase_id', 'transaction_id', 'trb_user_id', 'telegram_user_id',
        'telegram_username', 'matched_user__username', 'matched_user__email',
        'matched_team__name', 'matched_team__visible_name',
    )
    raw_id_fields = ('matched_user', 'matched_team')
    readonly_fields = (
        'purchase_id', 'transaction_id', 'product_id', 'product_name', 'amount', 'currency',
        'trb_user_id', 'telegram_user_id', 'telegram_username', 'purchase_created_at',
        'normalized_payload', 'payment_intent', 'ticket_request', 'received_at',
        'processed_at', 'refunded_at', 'refund_reason', 'ticket_revoked_at',
        'accounting_review_required', 'status', 'review_reason',
    )
    actions = (issue_tribute_purchase,)
    date_hierarchy = 'received_at'

    def has_add_permission(self, request):
        return False


@admin.register(TributePaymentIntent)
class TributePaymentIntentAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'status', 'user', 'team', 'telegram_user_id', 'expected_product_id',
        'expected_amount', 'expected_currency', 'ticket_type', 'expires_at',
    )
    list_filter = ('status', 'ticket_type', 'expected_currency', 'expected_product_id')
    search_fields = ('user__username', 'team__name', 'telegram_user_id', 'ticket_request__id')
    readonly_fields = [field.name for field in TributePaymentIntent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ClubSubscription)
class ClubSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'updated_at', 'user', 'provider', 'plan', 'status', 'currency', 'amount', 'auto_renew',
        'paid_until', 'next_charge_at', 'tribute_subscription_id', 'telegram_user_id',
        'duplicate_detected', 'last_webhook_event',
    )
    list_filter = ('provider', 'plan', 'status', 'currency', 'auto_renew', 'duplicate_detected')
    search_fields = (
        'user__username', 'user__email', 'telegram_user_id', 'tribute_subscription_id',
    )
    raw_id_fields = ('user',)
    readonly_fields = [field.name for field in ClubSubscription._meta.fields]
    date_hierarchy = 'updated_at'

    def has_add_permission(self, request):
        return False


@admin.register(ClubYooKassaPayment)
class ClubYooKassaPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'user', 'kind', 'status', 'amount', 'currency',
        'yookassa_payment_id', 'period_key', 'club_subscription',
    )
    list_filter = ('kind', 'status', 'currency')
    search_fields = ('yookassa_payment_id', 'idempotency_key', 'period_key', 'user__username')
    raw_id_fields = ('club_subscription', 'user')
    readonly_fields = [field.name for field in ClubYooKassaPayment._meta.fields]
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False


@admin.register(ClubSubscriptionEvent)
class ClubSubscriptionEventAdmin(admin.ModelAdmin):
    list_display = (
        'received_at', 'event_name', 'result', 'telegram_user_id',
        'tribute_subscription_id', 'club_subscription',
    )
    list_filter = ('event_name', 'result')
    search_fields = ('event_key', 'telegram_user_id', 'tribute_subscription_id')
    raw_id_fields = ('club_subscription',)
    readonly_fields = [field.name for field in ClubSubscriptionEvent._meta.fields]
    date_hierarchy = 'received_at'

    def has_add_permission(self, request):
        return False


@admin.register(SubscriptionGift)
class SubscriptionGiftAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'status', 'recipient_telegram_username', 'recipient_telegram_user_id',
        'duration_months', 'is_forever', 'created_by', 'created_by_telegram_user_id',
        'claimed_by', 'claimed_at', 'sent_at',
    )
    list_filter = ('status', 'is_forever')
    search_fields = ('recipient_telegram_username', 'recipient_telegram_user_id', 'created_by__username')
    raw_id_fields = ('created_by', 'claimed_by')
    readonly_fields = [field.name for field in SubscriptionGift._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.action(description='Подтвердить выбранные gift-платежи вручную')
def confirm_subscription_gift_payments(modeladmin, request, queryset):
    from games.subscription_gift_payments import manually_resolve_subscription_gift_payment

    resolved = sum(
        manually_resolve_subscription_gift_payment(payment.pk, succeeded=True)
        for payment in queryset
    )
    messages.success(request, 'Подтверждено gift-платежей: {}.'.format(resolved))


@admin.action(description='Отменить выбранные gift-платежи вручную')
def cancel_subscription_gift_payments(modeladmin, request, queryset):
    from games.subscription_gift_payments import manually_resolve_subscription_gift_payment

    resolved = sum(
        manually_resolve_subscription_gift_payment(payment.pk, succeeded=False)
        for payment in queryset
    )
    messages.success(request, 'Отменено gift-платежей: {}.'.format(resolved))


@admin.register(SubscriptionGiftPayment)
class SubscriptionGiftPaymentAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'status', 'provider', 'duration_months', 'expected_amount',
        'currency', 'purchaser', 'gift', 'provider_payment_id', 'purchase_id',
    )
    list_filter = ('status', 'provider', 'currency', 'duration_months')
    search_fields = ('provider_payment_id', 'purchase_id', 'purchaser__username')
    raw_id_fields = ('gift', 'purchaser')
    readonly_fields = [field.name for field in SubscriptionGiftPayment._meta.fields]
    actions = (confirm_subscription_gift_payments, cancel_subscription_gift_payments)

    def has_add_permission(self, request):
        return False


@admin.register(ClubEntitlement)
class ClubEntitlementAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'kind', 'starts_at', 'ends_at', 'gift', 'created_by', 'revoked_at')
    list_filter = ('kind', 'revoked_at')
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user', 'gift', 'club_subscription', 'yookassa_payment', 'tribute_event', 'created_by')
    readonly_fields = [field.name for field in ClubEntitlement._meta.fields]
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'status',
        'amount_rub',
        'pay_amount',
        'pay_currency',
        'user',
        'created_at',
        'confirmed_at',
    )
    list_filter = ('status', 'pay_currency')
    search_fields = ('public_token', 'nowpayments_id', 'pay_amount')
    readonly_fields = ('public_token', 'created_at', 'confirmed_at')
    raw_id_fields = ('user',)

    def changelist_view(self, request, extra_context=None):
        from games.donation_service import reject_stale_pending_donations

        reject_stale_pending_donations()
        return super().changelist_view(request, extra_context=extra_context)


def confirm_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        accept_ticket(ticket_request.pk, source='admin')


def reject_ticket_request(modeladmin, request, queryset):
    for ticket_request in queryset.all():
        reject_ticket(ticket_request.pk, source='admin')


confirm_ticket_request.short_description = "Confirm Ticket Request"
reject_ticket_request.short_description = "Reject Ticket Request"


@admin.register(PendingTicketRequest)
class PendingTicketRequestAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 40})},
    }
    raw_id_fields = ['team', 'created_by']

    def get_queryset(self, request):
        qs = super(PendingTicketRequestAdmin, self).get_queryset(request)
        return qs.filter(status='Pending')

    list_display = [
        '__str__', 'team', 'tickets', 'money', 'currency', 'payment_provider', 'merchant', 'status', 'time',
    ]
    list_filter = ('currency', 'payment_provider', 'merchant')
    actions = [confirm_ticket_request, reject_ticket_request]
