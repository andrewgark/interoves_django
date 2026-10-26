"""Compatibility alias. Implementation lives in games.ops.telegram.bot.callbacks."""
import sys

from games.ops.telegram.bot import callbacks as _implementation

sys.modules[__name__] = _implementation
