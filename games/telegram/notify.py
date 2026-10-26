"""Compatibility alias. Implementation lives in games.ops.telegram.bot.notify."""
import sys

from games.ops.telegram.bot import notify as _implementation

sys.modules[__name__] = _implementation
