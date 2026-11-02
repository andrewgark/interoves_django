"""Mutual friendships: requests, confirmation, invite links.

Every state change goes through this module so the incoming-request counter
in the header stays in sync with the database.
"""

import secrets

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from games.models import FriendInvite, Friendship

RELATION_SELF = 'self'
RELATION_NONE = 'none'
RELATION_OUTGOING = 'outgoing'
RELATION_INCOMING = 'incoming'
RELATION_FRIENDS = 'friends'

# Requests nobody answered pile up silently; a cap stops one account from
# sending requests to everybody in the results table.
MAX_PENDING_OUTGOING = 50
INCOMING_CACHE_SECONDS = 10 * 60


class FriendshipError(Exception):
    pass


def _pair(a_id, b_id):
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def _row(a_id, b_id):
    low, high = _pair(a_id, b_id)
    return Friendship.objects.filter(user_low_id=low, user_high_id=high).first()


def _incoming_cache_key(user_id):
    return 'friends-incoming:{}'.format(user_id)


def _touch(*user_ids):
    cache.delete_many([_incoming_cache_key(uid) for uid in user_ids])


def relation(viewer, other_user_id):
    if not getattr(viewer, 'is_authenticated', False):
        return RELATION_NONE
    if viewer.pk == other_user_id:
        return RELATION_SELF
    row = _row(viewer.pk, other_user_id)
    if row is None:
        return RELATION_NONE
    if row.status == Friendship.STATUS_ACCEPTED:
        return RELATION_FRIENDS
    return RELATION_OUTGOING if row.requested_by_id == viewer.pk else RELATION_INCOMING


def are_friends(a_id, b_id):
    if a_id is None or b_id is None or a_id == b_id:
        return False
    low, high = _pair(a_id, b_id)
    return Friendship.objects.filter(
        user_low_id=low, user_high_id=high, status=Friendship.STATUS_ACCEPTED,
    ).exists()


def _involving(user_id):
    return Friendship.objects.filter(Q(user_low_id=user_id) | Q(user_high_id=user_id))


def friend_user_ids(user_id):
    rows = _involving(user_id).filter(status=Friendship.STATUS_ACCEPTED).values_list('user_low_id', 'user_high_id')
    return {high if low == user_id else low for low, high in rows}


def incoming_requests(user_id):
    return _involving(user_id).filter(status=Friendship.STATUS_PENDING).exclude(requested_by_id=user_id)


def outgoing_requests(user_id):
    return Friendship.objects.filter(status=Friendship.STATUS_PENDING, requested_by_id=user_id)


def incoming_count(user_id):
    key = _incoming_cache_key(user_id)
    value = cache.get(key)
    if value is None:
        value = incoming_requests(user_id).count()
        cache.set(key, value, INCOMING_CACHE_SECONDS)
    return value


def _accept(row):
    row.status = Friendship.STATUS_ACCEPTED
    row.accepted_at = timezone.now()
    row.save(update_fields=['status', 'accepted_at'])
    _touch(row.user_low_id, row.user_high_id)
    return RELATION_FRIENDS


def request_friendship(viewer, other_user_id):
    """Send a request; answering someone's pending request accepts it."""
    if viewer.pk == other_user_id:
        raise FriendshipError('Нельзя добавить в друзья самого себя.')
    with transaction.atomic():
        row = _row(viewer.pk, other_user_id)
        if row is not None:
            if row.status == Friendship.STATUS_PENDING and row.requested_by_id != viewer.pk:
                return _accept(row)
            return relation(viewer, other_user_id)
        if outgoing_requests(viewer.pk).count() >= MAX_PENDING_OUTGOING:
            raise FriendshipError('Слишком много неотвеченных заявок. Подождите, пока на них ответят.')
        low, high = _pair(viewer.pk, other_user_id)
        try:
            with transaction.atomic():
                Friendship.objects.create(user_low_id=low, user_high_id=high, requested_by_id=viewer.pk)
        except IntegrityError:
            # A concurrent request for the same pair won; re-read its state.
            return relation(viewer, other_user_id)
    _touch(other_user_id)
    return RELATION_OUTGOING


def accept_request(viewer, other_user_id):
    row = _row(viewer.pk, other_user_id)
    if row is None or row.status != Friendship.STATUS_PENDING or row.requested_by_id == viewer.pk:
        raise FriendshipError('Заявка не найдена.')
    return _accept(row)


def remove(viewer, other_user_id):
    """Decline an incoming request, cancel an outgoing one or unfriend."""
    row = _row(viewer.pk, other_user_id)
    if row is not None:
        row.delete()
        _touch(viewer.pk, other_user_id)
    return RELATION_NONE


def active_invite(user):
    return FriendInvite.objects.filter(user=user, revoked_at__isnull=True).order_by('-created_at').first()


def new_invite(user):
    """Revoke the previous link and issue a fresh one."""
    with transaction.atomic():
        FriendInvite.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        return FriendInvite.objects.create(user=user, token=secrets.token_urlsafe(24))


def invite_by_token(token):
    return FriendInvite.objects.select_related('user', 'user__profile').filter(
        token=token, revoked_at__isnull=True,
    ).first()


def accept_invite(invite, viewer):
    """The link itself is the owner's consent, so friendship starts at once."""
    owner_id = invite.user_id
    if viewer.pk == owner_id:
        raise FriendshipError('Это ваша собственная ссылка.')
    with transaction.atomic():
        row = _row(viewer.pk, owner_id)
        if row is None:
            low, high = _pair(viewer.pk, owner_id)
            try:
                with transaction.atomic():
                    row = Friendship.objects.create(
                        user_low_id=low, user_high_id=high, requested_by_id=owner_id,
                    )
            except IntegrityError:
                row = _row(viewer.pk, owner_id)
        if row.status != Friendship.STATUS_ACCEPTED:
            _accept(row)
            FriendInvite.objects.filter(pk=invite.pk).update(uses_count=F('uses_count') + 1)
    _touch(viewer.pk, owner_id)
    return RELATION_FRIENDS
