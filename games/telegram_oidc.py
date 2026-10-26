"""Compatibility alias. Implementation lives in games.club.telegram_oidc."""
import sys

from games.club import telegram_oidc as _implementation

sys.modules[__name__] = _implementation
