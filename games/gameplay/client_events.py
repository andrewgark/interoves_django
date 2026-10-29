"""Client-generated event ids that make gameplay moves safe to resend."""

from __future__ import annotations

import uuid

from games.models import ClientEventReceipt
from games.site.http.exceptions import ClientEventConflictException

CLIENT_EVENT_ID_FIELD = 'client_event_id'

# The stored attempt must belong to the same actor and play context as the
# resent move; otherwise the id was reused or forged.
_ATTEMPT_IDENTITY_FIELDS = ('task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id')


def parse_client_event_id(post):
    """The UUID from a POST body, or None when absent or malformed."""
    raw = (post.get(CLIENT_EVENT_ID_FIELD) or '').strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def receipt_attempt(client_event_id, attempt):
    """The attempt this event id already produced, or None for a new id.

    Raises ``ClientEventConflictException`` when the id produced an attempt of
    another actor, task or replay.
    """
    receipt = (
        ClientEventReceipt.objects
        .select_related('attempt')
        .filter(client_event_id=client_event_id)
        .first()
    )
    if receipt is None:
        return None
    stored = receipt.attempt
    for field in _ATTEMPT_IDENTITY_FIELDS:
        if getattr(stored, field) != getattr(attempt, field):
            raise ClientEventConflictException(field)
    return stored
