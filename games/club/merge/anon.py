"""Compatibility alias. Implementation lives in games.identity.merge.anon_steps."""
import sys

from games.identity.merge import anon_steps as _implementation

sys.modules[__name__] = _implementation
