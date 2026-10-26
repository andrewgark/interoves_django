"""Compatibility alias. Implementation lives in games.identity.telegram_oidc."""
import sys

from games.identity import telegram_oidc as _implementation

sys.modules[__name__] = _implementation
