"""Compatibility alias. Implementation lives in games.ops.telegram.bot.reports."""
import sys

from games.ops.telegram.bot import reports as _implementation

sys.modules[__name__] = _implementation
