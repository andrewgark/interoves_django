"""Runtime switches for Цензурки."""

from __future__ import annotations


def open_pronouns_enabled() -> bool:
    """Whether unambiguous pronouns are visible at puzzle start."""
    from django.conf import settings
    return bool(getattr(settings, 'CENSORLY_OPEN_PRONOUNS', False))
