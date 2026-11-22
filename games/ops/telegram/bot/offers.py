"""Deciding on offered puzzles from the admin chat.

The offer message carries "Принять" and "На доработку" (Censorly: "Не брать").
Accepting is one tap and uses the same service as /support: the puzzle goes
to the end of the schedule, the author is notified. Sending back for revision
needs a note for the author, so the button posts a prompt and the note is a
Telegram reply to that prompt (see ``replies.py``).
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger('application')

DOMAIN = 'offer'
NO_NOTE = '-'
# "↩️ На доработку: лесенка #12" — the first line of the prompt the bot posts.
REVISION_PROMPT_RE = re.compile(r'^\S+\s+На доработку:\s+(\S+)\s+#(\d+)')


@dataclass(frozen=True)
class OfferKind:
    key: str
    label: str              # in prompts: "лесенка #12"
    model_path: str
    accept: Callable
    revise: Callable | None  # (offer, note) -> offer
    reject: Callable | None  # (offer, note) -> offer
    accepted_word: str      # "Принята"


def _ladder():
    from games.club.offers import ladder

    return ladder.accept_offer, ladder.request_revision


def _salad():
    from games.club.offers import salad

    return salad.accept_offer, salad.request_revision


def _alphabetty():
    from games.club.offers import alphabetty

    return alphabetty.accept_offer, alphabetty.request_revision


def _censorly_accept(offer):
    from games.console.games.censorly import accept_censorly_offer

    return accept_censorly_offer(offer.pk)


def _censorly_reject(offer, note):
    from games.console.games.censorly import reject_censorly_offer

    return reject_censorly_offer(offer.pk, note=note)


def _kinds() -> dict[str, OfferKind]:
    ladder_accept, ladder_revise = _ladder()
    salad_accept, salad_revise = _salad()
    alphabetty_accept, alphabetty_revise = _alphabetty()
    return {
        'ladder': OfferKind('ladder', 'лесенка', 'LadderOffer', ladder_accept,
                            lambda offer, note: ladder_revise(offer, admin_note=note), None, 'Принята'),
        'salad': OfferKind('salad', 'салатик', 'WordSaladOffer', salad_accept,
                           lambda offer, note: salad_revise(offer, admin_note=note), None, 'Принят'),
        'alphabetty': OfferKind('alphabetty', 'алфавитка', 'AlphabettyOffer', alphabetty_accept,
                                lambda offer, note: alphabetty_revise(offer, admin_note=note), None, 'Принята'),
        'censorly': OfferKind('censorly', 'цензурка', 'CensorlyOffer', _censorly_accept,
                              None, _censorly_reject, 'Принята'),
    }


KIND_BY_LABEL = {'лесенка': 'ladder', 'салатик': 'salad', 'алфавитка': 'alphabetty', 'цензурка': 'censorly'}


def offer_keyboard(kind: str, offer_id: int) -> dict:
    second = (
        {'text': '✖️ Не брать', 'callback_data': '{}:{}.reject:{}'.format(DOMAIN, kind, offer_id)}
        if kind == 'censorly'
        else {'text': '↩️ На доработку', 'callback_data': '{}:{}.revise:{}'.format(DOMAIN, kind, offer_id)}
    )
    return {'inline_keyboard': [[
        {'text': '✅ Принять', 'callback_data': '{}:{}.accept:{}'.format(DOMAIN, kind, offer_id)},
        second,
    ]]}


def _load(kind: OfferKind, offer_id: int):
    from games import models

    return getattr(models, kind.model_path).objects.filter(pk=offer_id).first()


def _accepted_number(offer) -> str:
    link = getattr(offer, 'accepted_link', None)
    return ' №{}'.format(link.number) if link is not None else ''


def handle_offer_callback(action: str, offer_id: int, callback_id, chat_id, message_id) -> None:
    from games.ops.telegram.bot.notify import resolved_keyboard
    from games.ops.telegram.transport.api import (
        answer_callback_query,
        edit_message_reply_markup,
        send_message,
    )

    key, _, verb = action.partition('.')
    kind = _kinds().get(key)
    if kind is None or verb not in ('accept', 'revise', 'reject'):
        answer_callback_query(callback_id, 'Неизвестное действие')
        return
    offer = _load(kind, offer_id)
    if offer is None:
        answer_callback_query(callback_id, 'Предложение не найдено', show_alert=True)
        return

    if verb == 'revise':
        # The note comes as a reply to this prompt; nothing changes until then.
        answer_callback_query(callback_id, 'Напишите комментарий реплаем')
        send_message(
            chat_id,
            '↩️ На доработку: {} #{}\n\nОтветьте на это сообщение: что поправить. '
            'Текст увидит автор. «{}» — вернуть без комментария.'.format(kind.label, offer_id, NO_NOTE),
            reply_markup={'force_reply': True, 'input_field_placeholder': 'Что поправить'},
            reply_to_message_id=message_id,
        )
        return

    try:
        if verb == 'accept':
            offer = kind.accept(offer)
            label = '✅ {}{}'.format(kind.accepted_word, _accepted_number(offer))
        else:
            if kind.reject is None:
                answer_callback_query(callback_id, 'Неизвестное действие')
                return
            kind.reject(offer, '')
            label = '✖️ Не взяли'
    except Exception as exc:
        # Service errors carry a ready Russian message ("уже обработано", …).
        logger.info('telegram offer decision refused kind=%s id=%s: %s', key, offer_id, exc)
        answer_callback_query(callback_id, str(exc)[:190] or 'Не получилось', show_alert=True)
        return
    answer_callback_query(callback_id, label)
    if chat_id and message_id:
        edit_message_reply_markup(chat_id, message_id, reply_markup=resolved_keyboard(label, DOMAIN, offer_id))


def handle_revision_reply(message: dict, prompt_text: str) -> bool:
    """A reply to the "На доработку" prompt: send the offer back with that note."""
    from games.ops.telegram.transport.api import send_message

    match = REVISION_PROMPT_RE.match((prompt_text or '').strip().split('\n', 1)[0])
    if not match:
        return False
    kind = _kinds().get(KIND_BY_LABEL.get(match.group(1), ''))
    if kind is None or kind.revise is None:
        return False
    offer_id = int(match.group(2))
    chat_id = (message.get('chat') or {}).get('id')
    reply_to = message.get('message_id')
    note = (message.get('text') or '').strip()
    if note == NO_NOTE:
        note = ''
    offer = _load(kind, offer_id)
    if offer is None:
        send_message(chat_id, 'Предложение #{} не найдено.'.format(offer_id), reply_to_message_id=reply_to)
        return True
    try:
        kind.revise(offer, note)
    except Exception as exc:
        send_message(
            chat_id, '{} #{}: не получилось — {}'.format(
                kind.label.capitalize(), offer_id, html.escape(str(exc), quote=False),
            ),
            reply_to_message_id=reply_to,
        )
        return True
    send_message(
        chat_id, '↩️ {} #{}: возвращено автору на доработку.'.format(kind.label.capitalize(), offer_id),
        reply_to_message_id=reply_to,
    )
    return True
