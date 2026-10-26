"""Compatibility alias. Implementation lives in games.ops.telegram.bot.commands."""
import sys

from games.ops.telegram.bot import commands as _implementation

sys.modules[__name__] = _implementation
