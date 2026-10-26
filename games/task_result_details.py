"""Compatibility alias. Implementation lives in games.tasks.result_details."""
import sys

from games.tasks import result_details as _implementation

sys.modules[__name__] = _implementation
