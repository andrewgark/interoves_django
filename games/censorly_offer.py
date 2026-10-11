"""Player submissions of Wikipedia articles for the daily Censorly schedule."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction

from games.ladder_offer import profile_ready_for_offers
from games.models import CensorlyOffer, RandomCensorlyGame


@dataclass(frozen=True)
class CensorlyOfferRow:
    id: int
    wiki_title: str
    share_hash: str
    play_url: str
    status: str
    status_label: str
    comment: str
    admin_note: str
    created_at: Optional[str]
    accepted_number: Optional[str]

    def to_dict(self):
        return asdict(self)


@transaction.atomic
def create_offer(user: User, party: RandomCensorlyGame, *, comment: str = '') -> CensorlyOffer:
    if not hasattr(user, 'profile'):
        raise ValueError('Нужен профиль')
    ready, _missing = profile_ready_for_offers(user.profile)
    if not ready:
        raise ValueError('Заполните профиль и привяжите Telegram')
    party = RandomCensorlyGame.objects.select_for_update().get(pk=party.pk)
    if CensorlyOffer.objects.filter(user=user, party=party, status=CensorlyOffer.STATUS_SENT).exists():
        raise ValueError('Вы уже предложили эту статью')
    return CensorlyOffer.objects.create(user=user, party=party, comment=(comment or '').strip()[:4000])


def serialize_offer(offer: CensorlyOffer) -> CensorlyOfferRow:
    created = offer.created_at.strftime('%Y-%m-%d') if offer.created_at else None
    number = offer.accepted_link.number if offer.accepted_link_id else None
    return CensorlyOfferRow(
        id=offer.pk,
        wiki_title=offer.party.wiki_title,
        share_hash=offer.party.share_hash,
        play_url=offer.party.play_url(),
        status=offer.status,
        status_label=offer.status_label,
        comment=offer.comment or '',
        admin_note=offer.admin_note or '',
        created_at=created,
        accepted_number=number,
    )


def list_user_offers(user: User) -> list[CensorlyOfferRow]:
    offers = CensorlyOffer.objects.filter(user=user).select_related('party', 'accepted_link')[:100]
    return [serialize_offer(offer) for offer in offers]
