"""Предложения лесенок от пользователей (offer → accept в расписание)."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from typing import Any, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from games.ladder_daily import LADDER_GAME_ID, ladder_publish_at
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    GameTaskGroup,
    HintAttempt,
    LadderOffer,
    Profile,
    Task,
    TaskGroup,
)
from games.raddle import ensure_raddle_assist_hints
from games.support.services.ladders import (
    AUTHOR_TAG,
    LadderSupportError,
    _normalize_intro,
    _parse_task_payload,
    attach_existing_task_group,
    build_checker_payload,
    get_ladder_game,
    list_ladder_rows,
    validate_ladder_content,
)

# Зарезервированные сегменты /ladder/<seg>/ — не генерировать как share_hash.
_RESERVED_LADDER_SEGMENTS = frozenset({
    'today', 'last', 'progress', 'results', 'teaser', 'o', 'share', 'offer',
})
_SHARE_HASH_RE = re.compile(r'^[a-f0-9]{16,32}$')
_TELEGRAM_HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')


class LadderOfferError(Exception):
    """Ошибка операции с предложением лесенки."""


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
        token = secrets.token_hex(8)  # 16 hex chars
        if token in _RESERVED_LADDER_SEGMENTS:
            continue
        if not LadderOffer.objects.filter(share_hash=token).exists():
            return token
    raise LadderOfferError('Не удалось сгенерировать share_hash')


def is_share_hash_segment(segment: str) -> bool:
    seg = (segment or '').strip().lower()
    if not seg or seg in _RESERVED_LADDER_SEGMENTS:
        return False
    if seg.isdigit():
        return False
    return bool(_SHARE_HASH_RE.match(seg))


def get_offer_by_share_hash(share_hash: str) -> Optional[LadderOffer]:
    seg = (share_hash or '').strip().lower()
    if not is_share_hash_segment(seg):
        return None
    return (
        LadderOffer.objects.select_related('task_group', 'user', 'accepted_link')
        .filter(share_hash=seg)
        .first()
    )


def _task_for_offer(offer: LadderOffer) -> Optional[Task]:
    tg = offer.task_group
    # Prefetch cache (list_user_offers / list_sent_offers).
    cache = getattr(tg, '_prefetched_objects_cache', None) or {}
    if 'tasks' in cache:
        for t in tg.tasks.all():
            if str(t.number) == '1':
                return t
        return None
    return Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()


def _offers_queryset_base():
    from django.db.models import Prefetch
    return LadderOffer.objects.select_related(
        'task_group', 'accepted_link', 'user', 'user__profile',
    ).prefetch_related(
        Prefetch('task_group__tasks', queryset=Task.objects.filter(number='1')),
    )


def _apply_content_to_task(
    task: Task,
    *,
    words: list[str],
    hints: list[str],
    intro: str,
    author: str,
    mixed_script: bool,
) -> None:
    errors = validate_ladder_content(words, hints, mixed_script=mixed_script)
    if errors:
        raise LadderOfferError('; '.join(errors))
    payload = build_checker_payload(words, hints, mixed_script=mixed_script)
    checker = CheckerType.objects.get(id='raddle')
    tags = dict(task.tags or {})
    if author.strip():
        tags[AUTHOR_TAG] = author.strip()
    else:
        tags.pop(AUTHOR_TAG, None)
    task.task_type = 'raddle'
    task.checker = checker
    task.checker_data = json.dumps(payload, ensure_ascii=False)
    task.answer = '\n'.join(payload['words'])
    task.text = _normalize_intro(intro)
    task.tags = tags
    task.points = 1
    task.max_attempts = None
    task.is_removed = False
    task.save()
    ensure_raddle_assist_hints(task)


@dataclass
class OfferRow:
    id: int
    status: str
    status_label: str
    share_hash: str
    play_url: str
    results_url: str
    author: str
    intro: str
    comment: str
    admin_note: str
    mixed_script: bool
    word_count: int
    words_preview: str
    words: list[str]
    hints: list[str]
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


def serialize_offer(offer: LadderOffer) -> OfferRow:
    task = _task_for_offer(offer)
    payload = _parse_task_payload(task)
    prod_number = None
    prod_date = None
    if offer.accepted_link_id:
        try:
            prod_number = int(offer.accepted_link.number)
        except (TypeError, ValueError, AttributeError):
            prod_number = None
        if prod_number:
            pub = ladder_publish_at(get_ladder_game(), prod_number)
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
        results_url=offer.results_url(),
        author=payload.get('author') or offer.author or '',
        intro=payload.get('intro') if payload.get('intro') is not None else (offer.intro or ''),
        comment=offer.comment or '',
        admin_note=offer.admin_note or '',
        mixed_script=bool(payload.get('mixed_script') or offer.mixed_script),
        word_count=int(payload.get('word_count') or 0),
        words_preview=payload.get('words_preview') or '',
        words=list(payload.get('words') or []),
        hints=list(payload.get('hints') or []),
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
def create_offer(user: User, *, author: str = '') -> LadderOffer:
    if not hasattr(user, 'profile'):
        raise LadderOfferError('Нужен профиль')
    ready, missing = profile_ready_for_offers(user.profile)
    if not ready:
        raise LadderOfferError('Профиль не заполнен: {}'.format(', '.join(missing)))
    checker = CheckerType.objects.get(id='raddle')
    display = author.strip() or profile_display_name(user.profile)
    task_group = TaskGroup.objects.create(
        label='ladder_offer:pending',
        checker=checker,
        points=1,
        max_attempts=3,
    )
    payload = build_checker_payload(
        ['ОДИН', 'ДВА'],
        ['Заглушечная подсказка, включающая первое слово ____ и загадывающая второе слово ...'],
        mixed_script=False,
    )
    tags = {AUTHOR_TAG: display} if display else {}
    task = Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='raddle',
        checker=checker,
        checker_data=json.dumps(payload, ensure_ascii=False),
        answer='\n'.join(payload['words']),
        text='',
        tags=tags,
        points=1,
        max_attempts=None,
        is_removed=False,
    )
    ensure_raddle_assist_hints(task)
    offer = LadderOffer.objects.create(
        user=user,
        status=LadderOffer.STATUS_DRAFT,
        share_hash=_new_share_hash(),
        author=display,
        intro='',
        comment='',
        mixed_script=False,
        task_group=task_group,
    )
    return offer


@transaction.atomic
def update_offer_content(
    offer: LadderOffer,
    *,
    words: list[str],
    hints: list[str],
    intro: str = '',
    author: str = '',
    comment: str = '',
    mixed_script: bool = False,
    allow_non_draft: bool = False,
    reset_actor_user: Optional[User] = None,
) -> LadderOffer:
    if not allow_non_draft and not offer.can_author_edit():
        raise LadderOfferError('После отправки редактировать нельзя')
    task = _task_for_offer(offer)
    if task is None:
        raise LadderOfferError('Задание лесенки не найдено')
    _apply_content_to_task(
        task,
        words=words,
        hints=hints,
        intro=intro,
        author=author,
        mixed_script=mixed_script,
    )
    offer.intro = _normalize_intro(intro)
    offer.author = (author or '').strip()
    offer.comment = (comment or '').strip()
    offer.mixed_script = bool(mixed_script)
    offer.save(update_fields=['intro', 'author', 'comment', 'mixed_script', 'updated_at'])
    if reset_actor_user is not None:
        reset_raddle_progress(
            task=task,
            game_id=LADDER_GAME_ID,
            user=reset_actor_user,
        )
    return offer


@transaction.atomic
def send_offer(offer: LadderOffer) -> LadderOffer:
    if offer.status != LadderOffer.STATUS_DRAFT:
        raise LadderOfferError('Отправить можно только черновик')
    task = _task_for_offer(offer)
    payload = _parse_task_payload(task)
    if (payload.get('word_count') or 0) < 2:
        raise LadderOfferError('Добавьте слова лесенки')
    errors = validate_ladder_content(
        payload.get('words') or [],
        payload.get('hints') or [],
        mixed_script=bool(payload.get('mixed_script')),
    )
    if errors:
        raise LadderOfferError('; '.join(errors))
    # Не отправлять заглушку.
    words = [w.upper() for w in (payload.get('words') or [])]
    if words == ['ОДИН', 'ДВА']:
        raise LadderOfferError('Замените слова-заглушки на настоящую лесенку')
    offer.status = LadderOffer.STATUS_SENT
    offer.sent_at = timezone.now()
    offer.admin_note = ''
    offer.save(update_fields=['status', 'sent_at', 'admin_note', 'updated_at'])
    offer_id = offer.pk

    def _notify():
        try:
            from games.telegram.notify import notify_new_ladder_offer
            notify_new_ladder_offer(offer_id)
        except Exception:
            # Не валим отправку из‑за TG.
            import logging
            logging.getLogger('application').exception(
                'Failed to notify admin about ladder offer %s', offer_id,
            )

    transaction.on_commit(_notify)
    return offer


@transaction.atomic
def accept_offer(offer: LadderOffer, *, at_number: int | None = None) -> LadderOffer:
    """Принять отправленную лесенку: привязать тот же TaskGroup к номеру в расписании."""
    from django.db import IntegrityError

    offer = (
        LadderOffer.objects.select_for_update()
        .select_related('task_group', 'accepted_link')
        .get(pk=offer.pk)
    )
    if offer.status != LadderOffer.STATUS_SENT:
        raise LadderOfferError('Принять можно только отправленную лесенку')
    if offer.accepted_link_id:
        # Уже в расписании (после доработки) — снова accepted.
        offer.status = LadderOffer.STATUS_ACCEPTED
        offer.accepted_at = timezone.now()
        offer.save(update_fields=['status', 'accepted_at', 'updated_at'])
        return offer
    rows = list_ladder_rows()
    max_num = max((r.number for r in rows), default=0)
    target = at_number if at_number is not None else (max_num + 1)
    try:
        link = attach_existing_task_group(offer.task_group, at_number=target)
    except LadderSupportError as exc:
        raise LadderOfferError(str(exc)) from exc
    except IntegrityError as exc:
        raise LadderOfferError(
            'Конфликт номера в расписании — обновите страницу и повторите'
        ) from exc
    offer.accepted_link = link
    offer.status = LadderOffer.STATUS_ACCEPTED
    offer.accepted_at = timezone.now()
    offer.save(update_fields=['accepted_link', 'status', 'accepted_at', 'updated_at'])
    return offer


@transaction.atomic
def request_revision(offer: LadderOffer, *, admin_note: str = '') -> LadderOffer:
    if offer.status not in (LadderOffer.STATUS_SENT, LadderOffer.STATUS_ACCEPTED):
        raise LadderOfferError('На доработку можно отправить только отправленную или принятую')
    # Контент на том же Task. Если слот уже вышел — автор править не сможет
    # (can_author_edit=False); правки через staff allow_non_draft.
    offer.status = LadderOffer.STATUS_DRAFT
    offer.admin_note = (admin_note or '').strip()
    offer.save(update_fields=['status', 'admin_note', 'updated_at'])
    return offer


def list_user_offers(user: User) -> list[OfferRow]:
    qs = _offers_queryset_base().filter(user=user).order_by('-updated_at')
    return [serialize_offer(o) for o in qs]


def list_sent_offers() -> list[OfferRow]:
    qs = (
        _offers_queryset_base()
        .filter(status=LadderOffer.STATUS_SENT)
        .order_by('-sent_at', '-updated_at')
    )
    return [serialize_offer(o) for o in qs]


def offer_for_link(link_id: int) -> Optional[LadderOffer]:
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


def reset_raddle_progress(
    *,
    task: Task,
    game_id: str = LADDER_GAME_ID,
    user: Optional[User] = None,
    team=None,
    anon_key: Optional[str] = None,
) -> int:
    """Удалить попытки / chain / hint attempts актора по заданию. Возвращает число Attempt."""
    from games.models import Game
    try:
        game = Game.objects.get(pk=game_id)
    except Game.DoesNotExist as exc:
        raise LadderOfferError('Игра не найдена') from exc

    attempt_qs = Attempt.manager.filter(task=task, game=game)
    chain_qs = ChainTaskState.objects.filter(task=task, game=game)
    hint_ids = list(task.hints.values_list('id', flat=True))
    hint_qs = HintAttempt.objects.filter(hint_id__in=hint_ids) if hint_ids else HintAttempt.objects.none()

    if team is not None:
        attempt_qs = attempt_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
        chain_qs = chain_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
        hint_qs = hint_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        attempt_qs = attempt_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
        chain_qs = chain_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
        hint_qs = hint_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        attempt_qs = attempt_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        chain_qs = chain_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        hint_qs = hint_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
    else:
        raise LadderOfferError('Нужен актор для сброса')

    n = attempt_qs.count()
    # Chain.last_attempt → SET_NULL, можно удалять attempts.
    chain_qs.delete()
    hint_qs.delete()
    attempt_qs.delete()
    return n


@transaction.atomic
def reset_all_raddle_progress(
    *,
    task: Task,
    game_id: str = LADDER_GAME_ID,
) -> dict[str, int]:
    """Сбросить прогресс всех акторов по заданию (попытки, chain, подсказки)."""
    from games.models import Game
    try:
        game = Game.objects.get(pk=game_id)
    except Game.DoesNotExist as exc:
        raise LadderOfferError('Игра не найдена') from exc

    attempt_qs = Attempt.manager.filter(task=task, game=game)
    chain_qs = ChainTaskState.objects.filter(task=task, game=game)
    hint_ids = list(task.hints.values_list('id', flat=True))
    hint_qs = (
        HintAttempt.objects.filter(hint_id__in=hint_ids)
        if hint_ids
        else HintAttempt.objects.none()
    )
    n_attempts = attempt_qs.count()
    n_chains = chain_qs.count()
    n_hints = hint_qs.count()
    chain_qs.delete()
    hint_qs.delete()
    attempt_qs.delete()
    return {
        'attempts': n_attempts,
        'chains': n_chains,
        'hint_attempts': n_hints,
    }


def offer_is_production_published(offer: LadderOffer, *, now=None) -> bool:
    """Принятая лесенка уже вышла по расписанию (МСК)."""
    if not offer.accepted_link_id:
        return False
    try:
        number = int(offer.accepted_link.number)
    except (TypeError, ValueError, AttributeError):
        return False
    from games.ladder_daily import is_ladder_number_published
    return is_ladder_number_published(get_ladder_game(), number, now)


def can_access_offer_hash(offer: LadderOffer, user, *, now=None) -> bool:
    """Hash-URL: до accept — всем; после accept до выхода — автору/staff; после выхода — всем."""
    if not offer.accepted_link_id:
        return True
    if not offer_is_production_published(offer, now=now):
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        if offer.user_id == user.id or getattr(user, 'is_staff', False):
            return True
        return False
    return True


def dashboard_offers_context() -> dict[str, Any]:
    offers = list_sent_offers()
    return {
        'sent_offers': offers,
        'sent_offers_json': [o.to_dict() for o in offers],
    }
