"""Предложения алфавиток от пользователей (offer -> accept в расписание)."""

from __future__ import annotations

import re
import secrets
from dataclasses import asdict, dataclass
from typing import Any, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from games.alphabetty.core import normalize_word
from games.alphabetty_daily import (
    ALPHABETTY_GAME_ID,
    alphabetty_publish_at,
)
from games.models import (
    AlphabettyOffer,
    Attempt,
    ChainTaskState,
    CheckerType,
    GameTaskGroup,
    Profile,
    Task,
    TaskGroup,
)
from games.support.services.alphabetty import (
    AlphabettySupportError,
    attach_existing_task_group,
    get_alphabetty_game,
    scheduled_words,
)

_RESERVED_ALPHABETTY_SEGMENTS = frozenset({
    'today', 'last', 'progress', 'suggest', 'guess', 'state', 'prefix', 'hint',
})
_SHARE_HASH_RE = re.compile(r'^[a-f0-9]{16,32}$')
_TELEGRAM_HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')
_DEFAULT_PLACEHOLDER = 'СЛОВО'


class AlphabettyOfferError(Exception):
    """Ошибка операции с предложением алфавитки."""


def normalize_telegram_handle(raw: str) -> str:
    value = (raw or '').strip()
    if value.startswith('@'):
        value = value[1:].strip()
    return value


def profile_ready_for_offers(profile: Profile) -> tuple[bool, list[str]]:
    missing = []
    if not (profile.first_name or '').strip():
        missing.append('first_name')
    if not (profile.last_name or '').strip():
        missing.append('last_name')
    handle = normalize_telegram_handle(profile.telegram_handle or '')
    if not handle:
        missing.append('telegram_handle')
    elif not _TELEGRAM_HANDLE_RE.match(handle):
        missing.append('telegram_handle_invalid')
    return (not missing, missing)


def profile_display_name(profile: Profile) -> str:
    return '{} {}'.format(
        (profile.first_name or '').strip(),
        (profile.last_name or '').strip(),
    ).strip()


def _new_share_hash() -> str:
    for _ in range(20):
        token = secrets.token_hex(8)
        if token in _RESERVED_ALPHABETTY_SEGMENTS:
            continue
        if not AlphabettyOffer.objects.filter(share_hash=token).exists():
            return token
    raise AlphabettyOfferError('Не удалось сгенерировать share_hash')


def is_share_hash_segment(segment: str) -> bool:
    seg = (segment or '').strip().lower()
    if not seg or seg in _RESERVED_ALPHABETTY_SEGMENTS:
        return False
    if seg.isdigit():
        return False
    return bool(_SHARE_HASH_RE.match(seg))


def get_offer_by_share_hash(share_hash: str) -> Optional[AlphabettyOffer]:
    seg = (share_hash or '').strip().lower()
    if not is_share_hash_segment(seg):
        return None
    return (
        AlphabettyOffer.objects.select_related('task_group', 'user', 'accepted_link')
        .filter(share_hash=seg)
        .first()
    )


def _task_for_offer(offer: AlphabettyOffer) -> Optional[Task]:
    return Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()


def _offers_queryset_base():
    return AlphabettyOffer.objects.select_related(
        'task_group', 'accepted_link', 'user', 'user__profile',
    )


def _normalize_offer_word(word: str) -> str:
    n = normalize_word(word)
    if not n:
        raise AlphabettyOfferError('Введите слово')
    if len(n) > 40:
        raise AlphabettyOfferError('Слишком длинное слово')
    if not re.match(r'^[А-Я]+$', n):
        raise AlphabettyOfferError('Только русские буквы')
    return n


def _apply_word_to_task(task: Task, *, word: str) -> None:
    normalized = _normalize_offer_word(word)
    checker = CheckerType.objects.get(id='alphabetty')
    task.task_type = 'alphabetty'
    task.checker = checker
    task.checker_data = normalized
    task.answer = normalized
    task.text = ''
    task.tags = {}
    task.points = 10
    task.max_attempts = None
    task.is_removed = False
    task.save()


@dataclass
class OfferRow:
    id: int
    status: str
    status_label: str
    share_hash: str
    play_url: str
    word: str
    comment: str
    admin_note: str
    created_at: Optional[str]
    updated_at: Optional[str]
    sent_at: Optional[str]
    accepted_at: Optional[str]
    production_number: Optional[int]
    production_publish_date: Optional[str]
    can_edit: bool
    user_id: int
    user_name: str
    telegram_handle: str
    task_id: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def serialize_offer(offer: AlphabettyOffer) -> OfferRow:
    task = _task_for_offer(offer)
    prod_number = None
    prod_date = None
    if offer.accepted_link_id:
        try:
            prod_number = int(offer.accepted_link.number)
        except (TypeError, ValueError, AttributeError):
            prod_number = None
        if prod_number:
            pub = alphabetty_publish_at(get_alphabetty_game(), prod_number)
            prod_date = pub.date().isoformat() if pub else None
    profile = getattr(offer.user, 'profile', None)
    tg = normalize_telegram_handle(getattr(profile, 'telegram_handle', '') or '')
    user_name = profile_display_name(profile) if profile else offer.user.username

    def _iso(dt):
        return dt.isoformat() if dt else None

    return OfferRow(
        id=offer.pk,
        status=offer.status,
        status_label=offer.status_label,
        share_hash=offer.share_hash,
        play_url=offer.play_url(),
        word=normalize_word((task.answer or '').strip()) if task else (offer.word or ''),
        comment=offer.comment or '',
        admin_note=offer.admin_note or '',
        created_at=_iso(offer.created_at),
        updated_at=_iso(offer.updated_at),
        sent_at=_iso(offer.sent_at),
        accepted_at=_iso(offer.accepted_at),
        production_number=prod_number,
        production_publish_date=prod_date,
        can_edit=offer.can_author_edit(),
        user_id=offer.user_id,
        user_name=user_name,
        telegram_handle=tg,
        task_id=task.pk if task else None,
    )


@transaction.atomic
def create_offer(user: User) -> AlphabettyOffer:
    if not hasattr(user, 'profile'):
        raise AlphabettyOfferError('Нужен профиль')
    ready, missing = profile_ready_for_offers(user.profile)
    if not ready:
        raise AlphabettyOfferError('Профиль не заполнен: {}'.format(', '.join(missing)))
    checker = CheckerType.objects.get(id='alphabetty')
    task_group = TaskGroup.objects.create(
        label='alphabetty_offer:pending',
        checker=checker,
        points=10,
        max_attempts=None,
    )
    Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='alphabetty',
        checker=checker,
        checker_data=_DEFAULT_PLACEHOLDER,
        answer=_DEFAULT_PLACEHOLDER,
        text='',
        tags={},
        points=10,
        max_attempts=None,
        is_removed=False,
    )
    return AlphabettyOffer.objects.create(
        user=user,
        status=AlphabettyOffer.STATUS_DRAFT,
        share_hash=_new_share_hash(),
        word=_DEFAULT_PLACEHOLDER,
        comment='',
        task_group=task_group,
    )


@transaction.atomic
def update_offer_content(
    offer: AlphabettyOffer,
    *,
    word: str,
    comment: str = '',
    allow_non_draft: bool = False,
) -> AlphabettyOffer:
    if not allow_non_draft and not offer.can_author_edit():
        raise AlphabettyOfferError('После отправки редактировать нельзя')
    task = _task_for_offer(offer)
    if task is None:
        raise AlphabettyOfferError('Задание алфавитки не найдено')
    normalized = _normalize_offer_word(word)
    _apply_word_to_task(task, word=normalized)
    offer.word = normalized
    offer.comment = (comment or '').strip()
    offer.save(update_fields=['word', 'comment', 'updated_at'])
    return offer


@transaction.atomic
def send_offer(offer: AlphabettyOffer) -> AlphabettyOffer:
    if offer.status != AlphabettyOffer.STATUS_DRAFT:
        raise AlphabettyOfferError('Отправить можно только черновик')
    task = _task_for_offer(offer)
    word = normalize_word((task.answer or '').strip()) if task else ''
    if not word:
        raise AlphabettyOfferError('Введите слово')
    if word == _DEFAULT_PLACEHOLDER:
        raise AlphabettyOfferError('Замените слово-заглушку на настоящее')
    offer.status = AlphabettyOffer.STATUS_SENT
    offer.sent_at = timezone.now()
    offer.admin_note = ''
    offer.save(update_fields=['status', 'sent_at', 'admin_note', 'updated_at'])
    offer_id = offer.pk

    def _notify():
        try:
            from games.telegram.notify import notify_new_alphabetty_offer
            notify_new_alphabetty_offer(offer_id)
        except Exception:
            import logging
            logging.getLogger('application').exception(
                'Failed to notify admin about alphabetty offer %s', offer_id,
            )

    transaction.on_commit(_notify)
    return offer


@transaction.atomic
def accept_offer(offer: AlphabettyOffer, *, at_number: int | None = None) -> AlphabettyOffer:
    from django.db import IntegrityError

    offer = (
        AlphabettyOffer.objects.select_for_update()
        .select_related('task_group', 'accepted_link')
        .get(pk=offer.pk)
    )
    if offer.status != AlphabettyOffer.STATUS_SENT:
        raise AlphabettyOfferError('Принять можно только отправленную алфавитку')
    task = _task_for_offer(offer)
    if task is None:
        raise AlphabettyOfferError('Задание алфавитки не найдено')
    word = normalize_word((task.answer or '').strip())
    if word in scheduled_words():
        raise AlphabettyOfferError(f'Слово уже занято другим днём: {word}')
    if offer.accepted_link_id:
        offer.status = AlphabettyOffer.STATUS_ACCEPTED
        offer.accepted_at = timezone.now()
        offer.save(update_fields=['status', 'accepted_at', 'updated_at'])
        return offer
    if at_number is None:
        rows = GameTaskGroup.objects.filter(game=get_alphabetty_game())
        max_num = 0
        for row in rows:
            try:
                max_num = max(max_num, int(row.number))
            except (TypeError, ValueError):
                pass
        at_number = max_num + 1
    try:
        link = attach_existing_task_group(offer.task_group, at_number=at_number)
    except AlphabettySupportError as exc:
        raise AlphabettyOfferError(str(exc)) from exc
    except IntegrityError as exc:
        raise AlphabettyOfferError(
            'Конфликт номера в расписании — обновите страницу и повторите'
        ) from exc
    offer.accepted_link = link
    offer.status = AlphabettyOffer.STATUS_ACCEPTED
    offer.accepted_at = timezone.now()
    offer.save(update_fields=['accepted_link', 'status', 'accepted_at', 'updated_at'])
    return offer


@transaction.atomic
def request_revision(offer: AlphabettyOffer, *, admin_note: str = '') -> AlphabettyOffer:
    if offer.status not in (AlphabettyOffer.STATUS_SENT, AlphabettyOffer.STATUS_ACCEPTED):
        raise AlphabettyOfferError('На доработку можно отправить только отправленную или принятую')
    offer.status = AlphabettyOffer.STATUS_DRAFT
    offer.admin_note = (admin_note or '').strip()
    offer.save(update_fields=['status', 'admin_note', 'updated_at'])
    return offer


def list_user_offers(user: User) -> list[OfferRow]:
    qs = _offers_queryset_base().filter(user=user).order_by('-updated_at')
    return [serialize_offer(o) for o in qs]


def list_sent_offers() -> list[OfferRow]:
    qs = (
        _offers_queryset_base()
        .filter(status=AlphabettyOffer.STATUS_SENT)
        .order_by('-sent_at', '-updated_at')
    )
    return [serialize_offer(o) for o in qs]


def offer_for_link(link_id: int) -> Optional[AlphabettyOffer]:
    return (
        _offers_queryset_base()
        .filter(accepted_link_id=link_id)
        .first()
    )


def offers_by_link_ids(link_ids: list[int]) -> dict[int, OfferRow]:
    if not link_ids:
        return {}
    qs = _offers_queryset_base().filter(accepted_link_id__in=link_ids)
    return {o.accepted_link_id: serialize_offer(o) for o in qs if o.accepted_link_id}


def offer_is_production_published(offer: AlphabettyOffer, *, now=None) -> bool:
    if not offer.accepted_link_id:
        return False
    try:
        number = int(offer.accepted_link.number)
    except (TypeError, ValueError, AttributeError):
        return False
    from games.alphabetty_daily import is_alphabetty_number_published
    return is_alphabetty_number_published(get_alphabetty_game(), number, now)


def can_access_offer_hash(offer: AlphabettyOffer, user, *, now=None) -> bool:
    if not offer.accepted_link_id:
        return True
    if not offer_is_production_published(offer, now=now):
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        if offer.user_id == user.id or getattr(user, 'is_staff', False):
            return True
        return False
    return True


@transaction.atomic
def reset_all_alphabetty_progress(*, task: Task, game_id: str = ALPHABETTY_GAME_ID) -> dict[str, int]:
    from games.models import Game
    try:
        game = Game.objects.get(pk=game_id)
    except Game.DoesNotExist as exc:
        raise AlphabettyOfferError('Игра не найдена') from exc

    attempt_qs = Attempt.manager.filter(task=task, game=game)
    chain_qs = ChainTaskState.objects.filter(task=task, game=game)
    n_attempts = attempt_qs.count()
    n_chains = chain_qs.count()
    chain_qs.delete()
    attempt_qs.delete()
    return {
        'attempts': n_attempts,
        'chains': n_chains,
        'hint_attempts': 0,
    }


def dashboard_offers_context() -> dict[str, Any]:
    offers = list_sent_offers()
    return {
        'sent_offers': offers,
        'sent_offers_json': [o.to_dict() for o in offers],
    }
