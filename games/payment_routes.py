"""Compatibility alias. Implementation lives in games.club.billing.routes."""
import sys

from games.club.billing import routes as _implementation

sys.modules[__name__] = _implementation
