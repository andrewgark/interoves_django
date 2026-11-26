"""Криптики от игроков: черновик → на проверке → принят редактором.

Редакторы — группа консоли «Cryptics» и support. Принятый криптик встаёт
в конец расписания через консоль раздела (те же проверки, что у дня,
созданного руками), автором дня записывается предложивший игрок.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from games.club.offers.profiles import profile_display_name, profile_ready_for_offers
from games.console.games.simple_daily import AUTHOR_FIELD, SimpleDailyError
from games.daily.catalog.cryptic import CRYPTIC_GAME_ID, answer_error
from games.models import CrypticOffer
from games.notifications.catalog import notifies_author
from games.puzzles.cryptic.explain import ExplainError, analyze, editor_preview, explanation_html

logger = logging.getLogger('application')

CLUE_MAX = 600
# Offers waiting for editors from one player: past this, sending waits.
MAX_PENDING_PER_USER = 10
ANSWER_MAX = 120
EXPLAIN_MAX = 1200
COMMENT_MAX = 4000


class CrypticOfferError(Exception):
    """Сообщение показывается игроку или редактору как есть."""


@dataclass
class OfferRow:
    id: int
    status: str
    status_label: str
    share_hash: str
    play_url: str
    clue: str
    answer: str
    enumeration: str
    shown_enumeration: str
    markup: str
    formula: str
    comment: str
    admin_note: str
    created_at: Optional[str]
    sent_at: Optional[str]
    accepted_number: Optional[str]
    can_edit: bool
    user_name: str
    reviewed_by: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value):
    return value.isoformat() if value else None


def _name(user) -> str:
    if user is None:
        return ''
    profile = getattr(user, 'profile', None)
    return profile_display_name(profile) if profile is not None else user.username


def serialize_offer(offer: CrypticOffer) -> OfferRow:
    return OfferRow(
        id=offer.pk,
        status=offer.status,
        status_label=offer.status_label,
        share_hash=offer.share_hash,
        play_url=offer.play_url(),
        clue=offer.clue,
        answer=offer.answer,
        enumeration=offer.enumeration,
        shown_enumeration=offer.shown_enumeration,
        markup=offer.markup,
        formula=offer.formula,
        comment=offer.comment,
        admin_note=offer.admin_note,
        created_at=_iso(offer.created_at),
        sent_at=_iso(offer.sent_at),
        accepted_number=offer.accepted_link.number if offer.accepted_link_id else None,
        can_edit=offer.can_author_edit(),
        user_name=_name(offer.user),
        reviewed_by=_name(offer.reviewed_by),
    )


def _base_queryset():
    return CrypticOffer.objects.select_related('user', 'user__profile', 'accepted_link', 'reviewed_by__profile')


def list_user_offers(user: User) -> list[OfferRow]:
    return [serialize_offer(o) for o in _base_queryset().filter(user=user).order_by('-updated_at')[:200]]


def list_sent_offers() -> list[OfferRow]:
    qs = _base_queryset().filter(status=CrypticOffer.STATUS_SENT).order_by('sent_at')
    return [serialize_offer(o) for o in qs]


def sent_offers_for_console() -> list[dict[str, Any]]:
    """Предложения на проверке с готовым HTML разбора для консоли редакторов."""
    out = []
    for row in list_sent_offers():
        data = row.to_dict()
        data['explanation_html'] = explanation_html(
            clue=row.clue, answer=row.answer, markup=row.markup, formula=row.formula,
        )
        out.append(data)
    return out


def get_offer_by_share_hash(share_hash: str) -> Optional[CrypticOffer]:
    from games.sections.placement_share import is_share_hash_segment

    token = (share_hash or '').strip().lower()
    if not is_share_hash_segment(token):
        return None
    return _base_queryset().filter(share_hash=token).first()


def _new_share_hash() -> str:
    from games.sections.placement_share import allocate_share_hash

    try:
        return allocate_share_hash()
    except RuntimeError as exc:
        raise CrypticOfferError('Не удалось создать ссылку, попробуйте ещё раз') from exc


def create_offer(user: User) -> CrypticOffer:
    profile = getattr(user, 'profile', None)
    if profile is None:
        raise CrypticOfferError('Нужен профиль')
    ready, _missing = profile_ready_for_offers(profile)
    if not ready:
        raise CrypticOfferError('Заполните профиль и привяжите Telegram')
    return CrypticOffer.objects.create(user=user, share_hash=_new_share_hash())


def _clean(value, limit) -> str:
    return str(value or '').strip()[:limit]


@transaction.atomic
def update_offer_content(offer: CrypticOffer, *, clue='', answer='', enumeration='', markup='',
                         formula='', comment='') -> CrypticOffer:
    """Черновик сохраняется как есть (автосохранение); проверка — при отправке."""
    offer = CrypticOffer.objects.select_for_update().get(pk=offer.pk)
    if not offer.can_author_edit():
        raise CrypticOfferError('Криптик уже у редакторов: редактировать нельзя')
    offer.clue = _clean(clue, CLUE_MAX)
    # Shown to players after solving, like the editors' answers: in capitals.
    offer.answer = ' '.join(_clean(answer, ANSWER_MAX).upper().split())
    offer.enumeration = _clean(enumeration, 64)
    offer.markup = _clean(markup, EXPLAIN_MAX)
    offer.formula = _clean(formula, EXPLAIN_MAX)
    offer.comment = _clean(comment, COMMENT_MAX)
    offer.save(update_fields=['clue', 'answer', 'enumeration', 'markup', 'formula', 'comment', 'updated_at'])
    return offer


def check_offer(offer: CrypticOffer) -> None:
    """Готов ли криптик к отправке: подсказка, ответ, маска и сошедшийся разбор."""
    if not offer.clue:
        raise CrypticOfferError('Напишите подсказку')
    if not offer.answer:
        raise CrypticOfferError('Напишите ответ')
    # The answer is shown as-is after solving: no markup, only Russian words.
    error = answer_error(offer.answer, offer.enumeration)
    if error:
        raise CrypticOfferError(error)
    if not offer.markup or not offer.formula:
        raise CrypticOfferError('Добавьте разбор: разметку и формулу')
    res = analyze(clue=offer.clue, answer=offer.answer, markup=offer.markup, formula=offer.formula)
    if res.errors:
        raise CrypticOfferError('Разбор не сходится: ' + '; '.join(res.errors))


def preview(*, clue='', answer='', markup='', formula='') -> dict[str, Any]:
    """Как в консоли: HTML разбора, ошибки и предупреждения."""
    return editor_preview(
        clue=_clean(clue, CLUE_MAX), answer=_clean(answer, ANSWER_MAX),
        markup=_clean(markup, EXPLAIN_MAX), formula=_clean(formula, EXPLAIN_MAX),
    )


@transaction.atomic
def send_offer(offer: CrypticOffer) -> CrypticOffer:
    offer = CrypticOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != CrypticOffer.STATUS_DRAFT:
        raise CrypticOfferError('Отправить можно только черновик')
    check_offer(offer)
    pending = CrypticOffer.objects.filter(user_id=offer.user_id, status=CrypticOffer.STATUS_SENT).count()
    if pending >= MAX_PENDING_PER_USER:
        raise CrypticOfferError(
            'У редакторов уже {} ваших криптиков. Дождитесь ответа по ним'.format(pending)
        )
    offer.status = CrypticOffer.STATUS_SENT
    offer.sent_at = timezone.now()
    offer.admin_note = ''
    offer.save(update_fields=['status', 'sent_at', 'admin_note', 'updated_at'])
    offer_id = offer.pk

    def _notify():
        try:
            from games.ops.telegram.bot.notify import notify_new_cryptic_offer

            notify_new_cryptic_offer(offer_id)
        except Exception:
            # Telegram must not fail the player's submission.
            logger.exception('Failed to notify editors about cryptic offer %s', offer_id)

    transaction.on_commit(_notify)
    return offer


@notifies_author('offer.accepted', 'cryptic')
@transaction.atomic
def accept_offer(offer: CrypticOffer, *, reviewer: User | None = None) -> CrypticOffer:
    """В конец расписания; автор дня — предложивший игрок."""
    from games.console.games.simple_daily_sections import console_for
    from games.models import TaskGroup
    from games.sections.placement_share import adopt_offer_share_hash

    offer = CrypticOffer.objects.select_for_update().select_related('user__profile').get(pk=offer.pk)
    if offer.status != CrypticOffer.STATUS_SENT:
        raise CrypticOfferError('Этот криптик уже обработан')
    try:
        check_offer(offer)
    except CrypticOfferError as exc:
        raise CrypticOfferError('Нельзя принять: {}'.format(exc)) from exc
    profile = getattr(offer.user, 'profile', None)
    console = console_for(CRYPTIC_GAME_ID)
    try:
        item = console.create(at_number=10 ** 6, fields={
            'clue': offer.clue,
            'answer': offer.answer,
            'enumeration': offer.enumeration,
            'markup': offer.markup,
            'formula': offer.formula,
            AUTHOR_FIELD: profile_display_name(profile) if profile is not None else offer.user.username,
        })
    except SimpleDailyError as exc:
        raise CrypticOfferError(str(exc)) from exc
    if profile is not None:
        TaskGroup.objects.get(pk=item['task_group_id']).authors.add(profile)
    from games.models import GameTaskGroup

    link = GameTaskGroup.objects.get(pk=item['link_id'])
    adopt_offer_share_hash(link, offer.share_hash)
    offer.accepted_link = link
    offer.status = CrypticOffer.STATUS_ACCEPTED
    offer.accepted_at = timezone.now()
    offer.reviewed_by = reviewer
    offer.save(update_fields=['accepted_link', 'status', 'accepted_at', 'reviewed_by', 'updated_at'])
    return offer


@notifies_author('offer.returned', 'cryptic')
@transaction.atomic
def request_revision(offer: CrypticOffer, *, admin_note: str = '', reviewer: User | None = None) -> CrypticOffer:
    offer = CrypticOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != CrypticOffer.STATUS_SENT:
        raise CrypticOfferError('Вернуть можно только криптик на проверке')
    offer.status = CrypticOffer.STATUS_DRAFT
    offer.admin_note = _clean(admin_note, COMMENT_MAX)
    offer.reviewed_by = reviewer
    offer.save(update_fields=['status', 'admin_note', 'reviewed_by', 'updated_at'])
    return offer


def _draft_checker(offer: CrypticOffer):
    """The day's checker: a draft accepts exactly what the accepted day will."""
    from games.puzzles.core.check_runtime import EqualsWithPossibleSpacesChecker

    return EqualsWithPossibleSpacesChecker(offer.answer)


def draft_client_spec(offer: CrypticOffer) -> dict[str, Any]:
    """Spec for the draft page to check answers itself (static/js/offline_verify.js)."""
    from games.puzzles.core.client_spec import hash_set_spec

    checker = _draft_checker(offer)
    # Per offer, like spec_salt per task: equal answers of two offers hash apart.
    salt = 'cryptic-offer:{}'.format(offer.pk)
    return hash_set_spec(salt, checker.bool_check.normalize_ops, dict.fromkeys(checker.data, 'Ok'))


def check_draft_answer(offer: CrypticOffer, text: str) -> dict[str, Any]:
    """Проверка на странице черновика: без попыток и без очков."""
    from games.puzzles.cryptic.explain import letters

    if not letters(text):
        return {'ok': False, 'empty': True}
    if not _draft_checker(offer).bool_check(text):
        return {'ok': False}
    explanation = ''
    if offer.markup and offer.formula:
        try:
            explanation = explanation_html(
                clue=offer.clue, answer=offer.answer, markup=offer.markup, formula=offer.formula,
            )
        except ExplainError:
            explanation = ''
    return {'ok': True, 'answer': offer.answer, 'explanation_html': explanation}
