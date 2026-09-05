"""Предложения салатиков от пользователей (идея или полный пазл → accept)."""

from __future__ import annotations

import re
import secrets
from dataclasses import asdict, dataclass
from typing import Any, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Profile,
    Task,
    TaskGroup,
    WordSaladOffer,
)
from games.word_salad import (
    WORD_SALAD_GAME_ID,
    format_grid_text,
    format_words_text,
    parse_grid,
    parse_task_data,
    parse_words,
    serialize_task_data,
    validate_puzzle,
)
from games.word_salad_daily import word_salad_publish_at
from games.support.services.word_salad import (
    WordSaladSupportError,
    attach_existing_task_group,
    ensure_word_salad_game,
    get_word_salad_game,
    list_word_salad_rows,
)

_RESERVED_SALAD_SEGMENTS = frozenset({
    'today', 'last', 'progress', 'results', 'teaser', 'o', 'share', 'offer',
})
_SHARE_HASH_RE = re.compile(r'^[a-f0-9]{16,32}$')
_TELEGRAM_HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')
_PLACEHOLDER_GRID = ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M']
_PLACEHOLDER_WORDS = ['ABCDEFGHIJKLMNOP']


class WordSaladOfferError(Exception):
    """Ошибка операции с предложением салатика."""


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
        if token in _RESERVED_SALAD_SEGMENTS or token.isdigit():
            continue
        if not WordSaladOffer.objects.filter(share_hash=token).exists():
            return token
    raise WordSaladOfferError('Не удалось сгенерировать share_hash')


def is_share_hash_segment(segment: str) -> bool:
    seg = (segment or '').strip().lower()
    if not seg or seg in _RESERVED_SALAD_SEGMENTS:
        return False
    if seg.isdigit():
        return False
    return bool(_SHARE_HASH_RE.match(seg))


def get_offer_by_share_hash(share_hash: str) -> Optional[WordSaladOffer]:
    seg = (share_hash or '').strip().lower()
    if not is_share_hash_segment(seg):
        return None
    return (
        WordSaladOffer.objects.select_related('task_group', 'user', 'accepted_link')
        .filter(
            share_hash=seg,
            kind=WordSaladOffer.KIND_FULL,
            task_group__isnull=False,
        )
        .first()
    )


def _task_for_offer(offer: WordSaladOffer) -> Optional[Task]:
    if not offer.task_group_id:
        return None
    tg = offer.task_group
    cache = getattr(tg, '_prefetched_objects_cache', None) or {}
    if 'tasks' in cache:
        for t in tg.tasks.all():
            if str(t.number) == '1':
                return t
        return None
    return Task.objects.filter(task_group_id=offer.task_group_id, number='1').first()


def _offers_queryset_base():
    return WordSaladOffer.objects.select_related(
        'task_group', 'accepted_link', 'user', 'user__profile',
    )


def _is_placeholder(grid, words) -> bool:
    if list(grid) != _PLACEHOLDER_GRID:
        return False
    normalized = [''.join(ch for ch in str(w).upper() if ch.isalpha()) for w in words]
    return normalized == _PLACEHOLDER_WORDS


def _puzzle_is_playable(offer: WordSaladOffer, task: Optional[Task]) -> bool:
    if offer.kind != WordSaladOffer.KIND_FULL or task is None:
        return False
    try:
        grid, words = parse_task_data(task.checker_data, '')
        if _is_placeholder(grid, words):
            return False
        validate_puzzle(grid, words)
        return True
    except ValueError:
        return False


def _ensure_full_task(offer: WordSaladOffer) -> Task:
    task = _task_for_offer(offer)
    if task is None:
        raise WordSaladOfferError('Задание салатика не найдено')
    return task


def _apply_theme(task: Task, theme: str) -> None:
    task.text = (theme or '').strip()


def _placeholder_checker_data() -> str:
    return serialize_task_data(_PLACEHOLDER_GRID, _PLACEHOLDER_WORDS)


def _try_apply_draft_puzzle(task: Task, *, theme: str, grid_text: str, words_text: str) -> None:
    _apply_theme(task, theme)
    try:
        grid = parse_grid(grid_text)
        words = parse_words(words_text)
    except ValueError:
        # Не оставляем предыдущий валидный пазл: иначе hash/send смотрят в устаревший checker_data.
        task.checker_data = _placeholder_checker_data()
        task.answer = ''
        task.save(update_fields=['text', 'checker_data', 'answer'])
        return
    task.checker_data = serialize_task_data(grid, words)
    task.answer = ''
    task.save(update_fields=['text', 'checker_data', 'answer'])


@dataclass
class OfferRow:
    id: int
    kind: str
    kind_label: str
    status: str
    status_label: str
    share_hash: str
    play_url: str
    theme: str
    idea_text: str
    suggested_words: str
    grid_text: str
    words_text: str
    comment: str
    admin_note: str
    created_at: Optional[str]
    updated_at: Optional[str]
    sent_at: Optional[str]
    accepted_at: Optional[str]
    production_number: Optional[int]
    production_publish_date: Optional[str]
    can_edit: bool
    is_playable: bool
    user_id: int
    user_name: str
    telegram_handle: str
    task_id: Optional[int]
    accepted_link_id: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def serialize_offer(offer: WordSaladOffer) -> OfferRow:
    task = _task_for_offer(offer)
    grid_text = offer.grid_text or ''
    words_text = offer.words_text or ''
    if not grid_text.strip() and not words_text.strip() and task is not None:
        try:
            grid, words = parse_task_data(task.checker_data, '')
            if not _is_placeholder(grid, words):
                grid_text = format_grid_text(grid)
                words_text = format_words_text(words)
        except ValueError:
            pass
    prod_number = None
    prod_date = None
    if offer.accepted_link_id:
        try:
            prod_number = int(offer.accepted_link.number)
        except (TypeError, ValueError, AttributeError):
            prod_number = None
        if prod_number:
            try:
                game = get_word_salad_game()
            except WordSaladSupportError:
                game = None
            if game is not None:
                pub = word_salad_publish_at(game, prod_number)
                prod_date = pub.date().isoformat() if pub else None
    profile = getattr(offer.user, 'profile', None)
    tg = normalize_telegram_handle(getattr(profile, 'telegram_handle', '') or '')
    user_name = profile_display_name(profile) if profile else offer.user.username

    def _iso(dt):
        return dt.isoformat() if dt else None

    return OfferRow(
        id=offer.pk,
        kind=offer.kind,
        kind_label=offer.kind_label,
        status=offer.status,
        status_label=offer.status_label,
        share_hash=offer.share_hash,
        play_url=offer.play_url() if _puzzle_is_playable(offer, task) else '',
        theme=offer.theme or '',
        idea_text=offer.idea_text or '',
        suggested_words=offer.suggested_words or '',
        grid_text=grid_text,
        words_text=words_text,
        comment=offer.comment or '',
        admin_note=offer.admin_note or '',
        created_at=_iso(offer.created_at),
        updated_at=_iso(offer.updated_at),
        sent_at=_iso(offer.sent_at),
        accepted_at=_iso(offer.accepted_at),
        production_number=prod_number,
        production_publish_date=prod_date,
        can_edit=offer.can_author_edit(),
        is_playable=_puzzle_is_playable(offer, task),
        user_id=offer.user_id,
        user_name=user_name,
        telegram_handle=tg,
        task_id=task.pk if task else None,
        accepted_link_id=offer.accepted_link_id,
    )


def _parse_kind(kind: str) -> str:
    value = (kind or WordSaladOffer.KIND_FULL).strip().lower()
    if value not in (WordSaladOffer.KIND_IDEA, WordSaladOffer.KIND_FULL):
        raise WordSaladOfferError('Неизвестный тип предложения')
    return value


@transaction.atomic
def create_offer(user: User, *, kind: str = WordSaladOffer.KIND_FULL) -> WordSaladOffer:
    if not hasattr(user, 'profile'):
        raise WordSaladOfferError('Нужен профиль')
    ready, missing = profile_ready_for_offers(user.profile)
    if not ready:
        raise WordSaladOfferError('Профиль не заполнен: {}'.format(', '.join(missing)))
    kind = _parse_kind(kind)
    if kind == WordSaladOffer.KIND_IDEA:
        return WordSaladOffer.objects.create(
            user=user,
            kind=kind,
            status=WordSaladOffer.STATUS_DRAFT,
            share_hash=_new_share_hash(),
        )
    try:
        checker = CheckerType.objects.get(id='word_salad')
    except CheckerType.DoesNotExist as exc:
        raise WordSaladOfferError('Тип проверки word_salad не найден') from exc
    task_group = TaskGroup.objects.create(
        label='salad_offer:pending',
        checker=checker,
        points=1,
        max_attempts=None,
        is_18_plus=False,
    )
    Task.objects.create(
        task_group=task_group,
        number='1',
        task_type='word_salad',
        checker=checker,
        checker_data=_placeholder_checker_data(),
        answer='',
        text='',
        tags={},
        points=1,
        max_attempts=None,
        is_removed=False,
    )
    return WordSaladOffer.objects.create(
        user=user,
        kind=kind,
        status=WordSaladOffer.STATUS_DRAFT,
        share_hash=_new_share_hash(),
        task_group=task_group,
    )


@transaction.atomic
def update_offer_content(
    offer: WordSaladOffer,
    *,
    theme: str = '',
    idea_text: str = '',
    suggested_words: str = '',
    grid_text: str = '',
    words_text: str = '',
    comment: str = '',
    allow_non_draft: bool = False,
) -> WordSaladOffer:
    if not allow_non_draft and not offer.can_author_edit():
        raise WordSaladOfferError('После отправки редактировать нельзя')
    offer.theme = (theme or '').strip()
    offer.comment = (comment or '').strip()
    if offer.kind == WordSaladOffer.KIND_IDEA:
        offer.idea_text = (idea_text or '').strip()
        offer.suggested_words = (suggested_words or '').strip()
        offer.save(update_fields=[
            'theme', 'idea_text', 'suggested_words', 'comment', 'updated_at',
        ])
        return offer
    offer.grid_text = grid_text or ''
    offer.words_text = words_text or ''
    task = _ensure_full_task(offer)
    _try_apply_draft_puzzle(
        task,
        theme=offer.theme,
        grid_text=offer.grid_text,
        words_text=offer.words_text,
    )
    offer.save(update_fields=['theme', 'grid_text', 'words_text', 'comment', 'updated_at'])
    return offer


@transaction.atomic
def send_offer(offer: WordSaladOffer) -> WordSaladOffer:
    if offer.status != WordSaladOffer.STATUS_DRAFT:
        raise WordSaladOfferError('Отправить можно только черновик')
    if offer.kind == WordSaladOffer.KIND_IDEA:
        if not (offer.theme or '').strip():
            raise WordSaladOfferError('Укажите тему')
        if not (offer.idea_text or '').strip():
            raise WordSaladOfferError('Опишите идею')
    else:
        try:
            grid, words = validate_puzzle(offer.grid_text, offer.words_text)
        except ValueError as exc:
            if not (offer.grid_text or '').strip() and not (offer.words_text or '').strip():
                raise WordSaladOfferError('Замените заглушку на настоящий салатик') from exc
            raise WordSaladOfferError(str(exc)) from exc
        if _is_placeholder(grid, words):
            raise WordSaladOfferError('Замените заглушку на настоящий салатик')
        if not (offer.theme or '').strip():
            raise WordSaladOfferError('Укажите тему')
        task = _ensure_full_task(offer)
        task.text = offer.theme
        task.checker_data = serialize_task_data(grid, words)
        task.answer = ''
        task.save(update_fields=['text', 'checker_data', 'answer'])
    offer.status = WordSaladOffer.STATUS_SENT
    offer.sent_at = timezone.now()
    offer.admin_note = ''
    offer.save(update_fields=['status', 'sent_at', 'admin_note', 'updated_at'])
    offer_id = offer.pk

    def _notify():
        try:
            from games.telegram.notify import notify_new_word_salad_offer
            notify_new_word_salad_offer(offer_id)
        except Exception:
            import logging
            logging.getLogger('application').exception(
                'Failed to notify admin about word salad offer %s', offer_id,
            )

    transaction.on_commit(_notify)
    return offer


@transaction.atomic
def accept_offer(offer: WordSaladOffer, *, at_number: int | None = None) -> WordSaladOffer:
    from django.db import IntegrityError

    offer = (
        WordSaladOffer.objects.select_for_update()
        .select_related('task_group', 'accepted_link')
        .get(pk=offer.pk)
    )
    if offer.status != WordSaladOffer.STATUS_SENT:
        raise WordSaladOfferError('Принять можно только отправленный салатик')
    if offer.kind == WordSaladOffer.KIND_IDEA:
        offer.status = WordSaladOffer.STATUS_ACCEPTED
        offer.accepted_at = timezone.now()
        offer.save(update_fields=['status', 'accepted_at', 'updated_at'])
        return offer
    if offer.accepted_link_id:
        offer.status = WordSaladOffer.STATUS_ACCEPTED
        offer.accepted_at = timezone.now()
        offer.save(update_fields=['status', 'accepted_at', 'updated_at'])
        return offer
    task = _ensure_full_task(offer)
    try:
        grid, words = parse_task_data(task.checker_data, '')
        validate_puzzle(grid, words)
    except ValueError as exc:
        raise WordSaladOfferError(str(exc)) from exc
    ensure_word_salad_game()
    rows = list_word_salad_rows()
    max_num = max((r.number for r in rows), default=0)
    target = at_number if at_number is not None else (max_num + 1)
    try:
        link = attach_existing_task_group(offer.task_group, at_number=target)
    except WordSaladSupportError as exc:
        raise WordSaladOfferError(str(exc)) from exc
    except IntegrityError as exc:
        raise WordSaladOfferError(
            'Конфликт номера в расписании — обновите страницу и повторите'
        ) from exc
    offer.accepted_link = link
    offer.status = WordSaladOffer.STATUS_ACCEPTED
    offer.accepted_at = timezone.now()
    offer.save(update_fields=['accepted_link', 'status', 'accepted_at', 'updated_at'])
    return offer


@transaction.atomic
def request_revision(offer: WordSaladOffer, *, admin_note: str = '') -> WordSaladOffer:
    if offer.status not in (WordSaladOffer.STATUS_SENT, WordSaladOffer.STATUS_ACCEPTED):
        raise WordSaladOfferError('На доработку можно отправить только отправленный или принятый')
    offer.status = WordSaladOffer.STATUS_DRAFT
    offer.admin_note = (admin_note or '').strip()
    offer.save(update_fields=['status', 'admin_note', 'updated_at'])
    return offer


def list_user_offers(user: User) -> list[OfferRow]:
    qs = _offers_queryset_base().filter(user=user).order_by('-updated_at')
    return [serialize_offer(o) for o in qs]


def list_sent_offers() -> list[OfferRow]:
    qs = (
        _offers_queryset_base()
        .filter(status=WordSaladOffer.STATUS_SENT)
        .order_by('-sent_at', '-updated_at')
    )
    return [serialize_offer(o) for o in qs]


def offer_for_link(link_id: int) -> Optional[WordSaladOffer]:
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


def can_access_offer_hash(offer: WordSaladOffer, user, *, now=None) -> bool:
    return True


def reset_salad_progress(
    *,
    task: Task,
    game_id: str = WORD_SALAD_GAME_ID,
    user: Optional[User] = None,
    team=None,
    anon_key: Optional[str] = None,
) -> int:
    from games.models import Game
    try:
        game = Game.objects.get(pk=game_id)
    except Game.DoesNotExist as exc:
        raise WordSaladOfferError('Игра не найдена') from exc

    attempt_qs = Attempt.manager.filter(task=task, game=game)
    chain_qs = ChainTaskState.objects.filter(task=task, game=game)
    if team is not None:
        attempt_qs = attempt_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
        chain_qs = chain_qs.filter(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        attempt_qs = attempt_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
        chain_qs = chain_qs.filter(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key:
        attempt_qs = attempt_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
        chain_qs = chain_qs.filter(anon_key=anon_key, team__isnull=True, user__isnull=True)
    else:
        raise WordSaladOfferError('Нужен актор для сброса')
    n = attempt_qs.count()
    chain_qs.delete()
    attempt_qs.delete()
    return n


@transaction.atomic
def reset_all_salad_progress(
    *,
    task: Task,
    game_id: str = WORD_SALAD_GAME_ID,
) -> dict[str, int]:
    from games.models import Game
    try:
        game = Game.objects.get(pk=game_id)
    except Game.DoesNotExist as exc:
        raise WordSaladOfferError('Игра не найдена') from exc

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
