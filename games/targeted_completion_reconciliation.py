"""Compatibility alias. Implementation lives in games.daily.completion.targeted_completion_reconciliation."""
import sys

from games.daily.completion import targeted_completion_reconciliation as _implementation

sys.modules[__name__] = _implementation
