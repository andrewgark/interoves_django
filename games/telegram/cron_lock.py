"""Compatibility alias. Implementation lives in games.ops.cron.telegram."""
import sys

from games.ops.cron import telegram as _implementation

sys.modules[__name__] = _implementation
