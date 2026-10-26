"""Compatibility alias. Implementation lives in games.task_group.result_details."""
import sys

from games.task_group import result_details as _implementation

sys.modules[__name__] = _implementation
