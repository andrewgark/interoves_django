"""Compatibility alias. Implementation lives in games.ops.telegram.bot.shadow."""
import sys

from games.ops.telegram.bot import shadow as _implementation

sys.modules[__name__] = _implementation
