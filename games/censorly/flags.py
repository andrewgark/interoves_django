"""Runtime switches for Цензурки.

``CENSORLY_LEXICAL_RESOLVER`` defaults off. Production does not set it.
"""

from __future__ import annotations


def lexical_resolver_enabled() -> bool:
    from django.conf import settings
    return bool(getattr(settings, 'CENSORLY_LEXICAL_RESOLVER', False))
