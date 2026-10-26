"""Compatibility alias. Implementation lives in games.ops.telegram.bot.alerts."""
import sys

from games.ops.telegram.bot import alerts as _implementation

sys.modules[__name__] = _implementation
