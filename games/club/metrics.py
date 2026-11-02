"""Club subscription counts for admin reports.

Access is read from the ``ClubEntitlement`` ledger. Every user with access is
counted once, under the strongest reason they have it: their own payment wins
over a gift, and a gift wins over a manual grant. The payment route comes from
the entitlement's payment link (YooKassa payment or Tribute event), because
``ClubSubscription.provider`` can keep its first value after a user switches
routes.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from games.models import ClubEntitlement, ClubSubscription

PROVIDER_YOOKASSA = ClubSubscription.PROVIDER_YOOKASSA
PROVIDER_TRIBUTE = ClubSubscription.PROVIDER_TRIBUTE


@dataclass
class ClubSnapshot:
    paid_yookassa: int = 0
    paid_tribute: int = 0
    gift_purchased: int = 0
    gift_admin: int = 0
    manual: int = 0
    paid_auto_renew_off: int = 0
    paid_ending_soon: int = 0  # paid access that ends within a week without auto-renew

    @property
    def paid(self):
        return self.paid_yookassa + self.paid_tribute

    @property
    def gifts(self):
        return self.gift_purchased + self.gift_admin

    @property
    def total(self):
        return self.paid + self.gifts + self.manual


@dataclass
class ClubPeriodStats:
    first_payments: dict = field(default_factory=lambda: {PROVIDER_YOOKASSA: 0, PROVIDER_TRIBUTE: 0})
    renewals: dict = field(default_factory=lambda: {PROVIDER_YOOKASSA: 0, PROVIDER_TRIBUTE: 0})
    auto_renew_cancellations: int = 0
    gifts_claimed: int = 0

    @property
    def first_payments_total(self):
        return sum(self.first_payments.values())

    @property
    def renewals_total(self):
        return sum(self.renewals.values())


def _active_at(at):
    return (
        ClubEntitlement.objects.filter(revoked_at__isnull=True, starts_at__lte=at)
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
    )


def entitlement_provider(entitlement):
    if entitlement.yookassa_payment_id:
        return PROVIDER_YOOKASSA
    if entitlement.tribute_event_id:
        return PROVIDER_TRIBUTE
    subscription = entitlement.club_subscription
    if subscription is not None and subscription.provider == PROVIDER_TRIBUTE:
        return PROVIDER_TRIBUTE
    return PROVIDER_YOOKASSA


def _gift_was_purchased(gift):
    return bool(gift and (gift.purchaser_id or gift.paid_at))


def club_snapshot(at):
    """Users with Club access at ``at``, each counted once."""
    by_user = {}
    rank = {ClubEntitlement.KIND_PAID: 0, ClubEntitlement.KIND_GIFT: 1, ClubEntitlement.KIND_MANUAL: 2}
    for entitlement in _active_at(at).select_related('gift', 'club_subscription'):
        current = by_user.get(entitlement.user_id)
        if current is None or rank[entitlement.kind] < rank[current.kind]:
            by_user[entitlement.user_id] = entitlement
    # Access granted beyond the latest entitlement (``paid_until`` set by a
    # provider) still has to count if the ledger has no active row for it.
    # ``paid_until`` is today's value, so for a past ``at`` this is an upper
    # bound; ``created_at`` at least keeps out subscriptions that did not exist.
    paid_without_ledger = (
        ClubSubscription.objects.filter(
            paid_until__gt=at, last_payment_at__isnull=False, created_at__lte=at,
        )
        .exclude(user_id__in=[uid for uid, e in by_user.items() if e.kind == ClubEntitlement.KIND_PAID])
    )
    subscriptions = {s.user_id: s for s in ClubSubscription.objects.filter(user_id__in=by_user)}

    snapshot = ClubSnapshot()
    week_later = at + timedelta(days=7)

    def count_paid(provider, subscription, paid_until):
        if provider == PROVIDER_TRIBUTE:
            snapshot.paid_tribute += 1
        else:
            snapshot.paid_yookassa += 1
        if subscription is not None and not subscription.auto_renew:
            snapshot.paid_auto_renew_off += 1
            if paid_until is not None and paid_until <= week_later:
                snapshot.paid_ending_soon += 1

    for user_id, entitlement in by_user.items():
        if entitlement.kind == ClubEntitlement.KIND_PAID:
            subscription = subscriptions.get(user_id)
            paid_until = max(
                filter(None, [entitlement.ends_at, subscription.paid_until if subscription else None]),
                default=None,
            )
            count_paid(entitlement_provider(entitlement), subscription, paid_until)
        elif entitlement.kind == ClubEntitlement.KIND_GIFT:
            if _gift_was_purchased(entitlement.gift):
                snapshot.gift_purchased += 1
            else:
                snapshot.gift_admin += 1
        else:
            snapshot.manual += 1
    for subscription in paid_without_ledger:
        if subscription.user_id in by_user:
            # Their gift/manual row was counted; a payment outranks it.
            previous = by_user[subscription.user_id]
            if previous.kind == ClubEntitlement.KIND_GIFT:
                if _gift_was_purchased(previous.gift):
                    snapshot.gift_purchased -= 1
                else:
                    snapshot.gift_admin -= 1
            else:
                snapshot.manual -= 1
        count_paid(subscription.provider, subscription, subscription.paid_until)
    return snapshot


def club_period_stats(since, until):
    """Payments, renewals, cancellations and gift claims recorded in [since, until)."""
    stats = ClubPeriodStats()
    paid = ClubEntitlement.objects.filter(kind=ClubEntitlement.KIND_PAID, revoked_at__isnull=True)
    in_period = list(
        paid.filter(created_at__gte=since, created_at__lt=until).select_related('club_subscription')
    )
    earlier_payers = set(
        paid.filter(created_at__lt=since, user_id__in={e.user_id for e in in_period})
        .values_list('user_id', flat=True)
    )
    # A subscription that was paid before the entitlement ledger existed is a
    # renewal too, not a first payment.
    earlier_payers |= set(
        ClubSubscription.objects.filter(
            user_id__in={e.user_id for e in in_period}, intro_offer_used_at__lt=since,
        ).values_list('user_id', flat=True)
    )
    seen = set()
    for entitlement in sorted(in_period, key=lambda e: e.created_at):
        provider = entitlement_provider(entitlement)
        if entitlement.user_id in earlier_payers or entitlement.user_id in seen:
            stats.renewals[provider] += 1
        else:
            stats.first_payments[provider] += 1
        seen.add(entitlement.user_id)
    stats.auto_renew_cancellations = ClubSubscription.objects.filter(
        cancelled_at__gte=since, cancelled_at__lt=until, auto_renew=False,
    ).count()
    stats.gifts_claimed = ClubEntitlement.objects.filter(
        kind=ClubEntitlement.KIND_GIFT, created_at__gte=since, created_at__lt=until,
    ).count()
    return stats


MEMBER_COUNT_CACHE_KEY = 'club-member-count'


def club_member_count():
    """Users with Club access now, cached for an hour (shown in the Club offer)."""
    count = cache.get(MEMBER_COUNT_CACHE_KEY)
    if count is None:
        count = club_snapshot(timezone.now()).total
        cache.set(MEMBER_COUNT_CACHE_KEY, count, 60 * 60)
    return count
