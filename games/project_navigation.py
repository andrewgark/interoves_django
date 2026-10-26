"""Compatibility alias. Implementation lives in games.sections.project_navigation."""
import sys

from games.sections import project_navigation as _implementation

sys.modules[__name__] = _implementation
