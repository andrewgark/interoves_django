"""Compatibility alias. Implementation lives in games.ops.telegram.bot.public."""
import sys

from games.ops.telegram.bot import public as _implementation

sys.modules[__name__] = _implementation
